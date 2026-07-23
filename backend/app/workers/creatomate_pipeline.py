from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.api.schemas.creatomate import CreatomateInstructions
from app.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.models import ItemStatus, Job, JobItem, JobStatus
from app.services.creatomate_service import CreatomateError, CreatomateService
from app.services.ffmpeg_service import FFmpegService
from app.services.storage import StorageService
from app.services.zip_service import ZipService
from app.api.routes.public_media import public_media_available, register_public_media

logger = get_logger(__name__)


class CreatomatePipeline:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.storage = StorageService()
        self.client = CreatomateService()
        self.ffmpeg = FFmpegService(self.storage)
        self.zip_service = ZipService(self.storage)
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_renders)

    async def process_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None or job.status == JobStatus.cancelled:
                return

            try:
                instructions = CreatomateInstructions.model_validate_json(
                    job.instructions_json or "{}"
                )
            except Exception as exc:
                job.status = JobStatus.failed
                job.error = f"Invalid Creatomate instructions: {exc}"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            items = db.query(JobItem).filter(JobItem.job_id == job_id).all()
            await asyncio.gather(
                *[
                    self._process_item(job_id, item.id, instructions, job.upload_id)
                    for item in items
                ]
            )

            db.expire_all()
            job = db.get(Job, job_id)
            assert job is not None
            if job.status == JobStatus.cancelled:
                return

            items = db.query(JobItem).filter(JobItem.job_id == job_id).all()
            failed = [i for i in items if i.status == ItemStatus.failed]
            completed = [i for i in items if i.status == ItemStatus.completed]

            if not completed:
                job.status = JobStatus.failed
                job.error = "All Creatomate renders failed"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            output_files = [Path(i.output_path) for i in completed if i.output_path]
            zip_path = self.zip_service.build_job_zip(job_id, output_files)
            job.zip_path = str(zip_path)
            if failed:
                job.status = JobStatus.partial
                job.error = f"{len(failed)} of {len(items)} items failed"
            else:
                job.status = JobStatus.completed
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "Creatomate job %s completed (%d ok, %d failed)",
                job_id,
                len(completed),
                len(failed),
            )
        except Exception as exc:
            logger.exception("Creatomate job %s crashed: %s", job_id, exc)
            job = db.get(Job, job_id)
            if job:
                job.status = JobStatus.failed
                job.error = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()

    async def _process_item(
        self,
        job_id: str,
        item_id: str,
        instructions: CreatomateInstructions,
        upload_id: str,
    ) -> None:
        async with self._semaphore:
            db = SessionLocal()
            try:
                job = db.get(Job, job_id)
                item = db.get(JobItem, item_id)
                if job is None or item is None:
                    return
                if job.status == JobStatus.cancelled:
                    item.status = ItemStatus.cancelled
                    db.commit()
                    return

                item.status = ItemStatus.running
                item.started_at = datetime.now(timezone.utc)
                item.progress = 5.0
                item.error = None
                db.commit()

                async def on_progress(pct: float) -> None:
                    pdb = SessionLocal()
                    try:
                        it = pdb.get(JobItem, item_id)
                        if it and it.status == ItemStatus.running:
                            it.progress = pct
                            pdb.commit()
                    finally:
                        pdb.close()

                if instructions.mode == "edit":
                    try:
                        payload, occurrences, preview_before = await self._build_edit_payload(
                            item=item,
                            instructions=instructions,
                            upload_id=upload_id,
                            job_id=job_id,
                            on_progress=on_progress,
                        )
                    except CreatomateError as host_exc:
                        if "publish upload" in str(host_exc).lower() or "temp host" in str(host_exc).lower():
                            logger.warning(
                                "Creatomate hosting unavailable (%s); falling back to local FFmpeg with same OCR regions",
                                host_exc,
                            )
                            await self._ffmpeg_fallback_edit(
                                item=item,
                                instructions=instructions,
                                upload_id=upload_id,
                                job_id=job_id,
                                on_progress=on_progress,
                                db=db,
                            )
                            return
                        raise
                    item.occurrences_replaced = occurrences
                    if preview_before:
                        item.preview_before_path = str(preview_before)
                else:
                    payload = await self._build_legacy_payload(
                        item=item,
                        instructions=instructions,
                        on_progress=on_progress,
                    )

                await on_progress(25.0)
                created = await self.client.create_render(payload)
                render_id = str(created.get("id") or "")
                if not render_id:
                    raise CreatomateError(f"Creatomate response missing id: {created}")

                finished = await self.client.wait_for_render(
                    render_id, on_progress=on_progress
                )
                url = finished.get("url")
                if not url:
                    raise CreatomateError("Creatomate succeeded but returned no URL")

                output_path = Path(item.output_path or "")
                await self.client.download_file(str(url), output_path)

                preview_dir = self.storage.output_dir(job_id) / "previews" / item_id
                preview_dir.mkdir(parents=True, exist_ok=True)
                snapshot = finished.get("snapshot_url")
                if snapshot:
                    after = preview_dir / "preview_after.jpg"
                    try:
                        await self.client.download_file(str(snapshot), after)
                        item.preview_after_path = str(after)
                    except Exception as exc:
                        logger.warning("Snapshot download failed: %s", exc)
                elif output_path.is_file():
                    after = preview_dir / "preview_after.png"
                    if await self.ffmpeg._extract_frame(output_path, after, at_seconds=1.0):
                        item.preview_after_path = str(after)

                item.status = ItemStatus.completed
                item.progress = 100.0
                item.finished_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("Creatomate item %s completed (render=%s)", item_id, render_id)
            except Exception as exc:
                logger.warning("Creatomate item %s failed: %s", item_id, exc)
                item = db.get(JobItem, item_id)
                if item:
                    item.error = str(exc)
                    item.status = ItemStatus.failed
                    item.finished_at = datetime.now(timezone.utc)
                    db.commit()
            finally:
                db.close()

    async def _build_edit_payload(
        self,
        *,
        item: JobItem,
        instructions: CreatomateInstructions,
        upload_id: str,
        job_id: str,
        on_progress,
    ) -> tuple[dict, int, Path | None]:
        edits = instructions.edits
        if edits is None:
            raise CreatomateError("Missing edit instructions")

        input_path = Path(item.input_path)
        if not input_path.is_file():
            raise CreatomateError(f"Input video not found: {item.input_path}")

        await on_progress(8.0)
        duration = await self.ffmpeg.probe_duration(input_path) or 5.0
        size = await self.ffmpeg.probe_video_size(input_path)
        if not size:
            raise CreatomateError("Could not probe video dimensions")
        width, height = size

        await on_progress(10.0)
        # Publish source first so hosting failures can fall back before expensive OCR.
        host_path = input_path
        lean: Path | None = None
        try:
            if public_media_available():
                video_url = register_public_media(input_path)
                logger.info("Using PUBLIC_BASE_URL media %s", video_url)
            else:
                if input_path.stat().st_size > 25 * 1024 * 1024:
                    await on_progress(12.0)
                    lean = await self.client.make_hostable_copy(
                        input_path, self.settings.ffmpeg_path
                    )
                    host_path = lean
                video_url = await self.client.upload_temp_public(host_path)
        finally:
            if lean is not None:
                try:
                    lean.unlink(missing_ok=True)
                    lean.parent.rmdir()
                except Exception:
                    pass

        preview_dir = self.storage.output_dir(job_id) / "previews" / item.id
        regions = []
        preview_before: Path | None = None
        if edits.replace_text:
            await on_progress(15.0)
            regions, preview_before, _ = await self.ffmpeg.locate_text_regions(
                input_path,
                edits.replace_text,
                preview_dir=preview_dir,
                progress_cb=on_progress,
            )
            if not regions:
                raise CreatomateError(
                    "No OCR text regions to replace. "
                    "The requested text was not found on sampled frames."
                )

        logo_url = None
        watermark_url = None
        if edits.replace_logo:
            logo_path = self.storage.resolve_asset(upload_id, edits.replace_logo)
            if logo_path and logo_path.is_file():
                logo_url = await self.client.upload_temp_public(logo_path)
        wm_name = edits.watermark_image or edits.replace_logo
        if edits.watermark is not None and wm_name:
            wm_path = self.storage.resolve_asset(upload_id, wm_name)
            if wm_path and wm_path.is_file():
                watermark_url = await self.client.upload_temp_public(wm_path)

        if not regions and not logo_url and edits.watermark is None:
            raise CreatomateError("Nothing to edit — OCR found no text and no logo/watermark")

        payload = self.client.build_ocr_edit_source(
            video_url=video_url,
            width=width,
            height=height,
            duration=duration,
            regions=regions,
            logo_url=logo_url,
            watermark_url=watermark_url,
            watermark_position=edits.watermark.value if edits.watermark else None,
        )
        return payload, len(regions), preview_before

    async def _ffmpeg_fallback_edit(
        self,
        *,
        item: JobItem,
        instructions: CreatomateInstructions,
        upload_id: str,
        job_id: str,
        on_progress,
        db,
    ) -> None:
        """When Creatomate cannot fetch the source video, burn edits with local FFmpeg."""
        edits = instructions.edits
        if edits is None:
            raise CreatomateError("Missing edit instructions for FFmpeg fallback")
        input_path = Path(item.input_path)
        output_path = Path(item.output_path or "")
        preview_dir = self.storage.output_dir(job_id) / "previews" / item.id
        await on_progress(30.0)
        result = await self.ffmpeg.render(
            input_path=input_path,
            output_path=output_path,
            instructions=edits,
            upload_id=upload_id,
            progress_cb=on_progress,
            preview_dir=preview_dir,
        )
        item.occurrences_replaced = result.occurrences
        if result.preview_before:
            item.preview_before_path = str(result.preview_before)
        if result.preview_after:
            item.preview_after_path = str(result.preview_after)
        item.status = ItemStatus.completed
        item.progress = 100.0
        item.finished_at = datetime.now(timezone.utc)
        item.error = None
        db.commit()
        logger.info(
            "Creatomate item %s completed via FFmpeg fallback (%d occurrence(s)); "
            "set PUBLIC_BASE_URL for true Creatomate cloud renders",
            item.id,
            result.occurrences,
        )

    async def _build_legacy_payload(
        self,
        *,
        item: JobItem,
        instructions: CreatomateInstructions,
        on_progress,
    ) -> dict:
        video_url: str | None = None
        input_path = Path(item.input_path)
        if item.input_path.startswith("creatomate://"):
            if instructions.video_urls:
                video_url = instructions.video_urls[0]
        elif input_path.is_file():
            await on_progress(12.0)
            video_url = await self.client.upload_temp_public(input_path)

        mods = dict(instructions.modifications)
        primary = instructions.text_primary or str(mods.get("Text-1") or "Your Text")
        secondary = instructions.text_secondary or str(mods.get("Text-2") or "Creatomate API")
        mods.setdefault("Text-1", primary)
        mods.setdefault("Text-2", secondary)
        if video_url:
            mods.setdefault("Video", video_url)

        if instructions.mode == "source":
            return self.client.build_source_payload(
                text_primary=primary,
                text_secondary=secondary,
                video_url=video_url,
            )

        template_id = instructions.template_id or self.settings.creatomate_default_template_id
        if not template_id:
            raise CreatomateError("No Creatomate template_id configured")
        return self.client.build_template_payload(
            template_id=template_id,
            modifications=mods,
        )
