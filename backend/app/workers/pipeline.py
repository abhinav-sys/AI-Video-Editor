from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.api.schemas.edits import EditInstructions
from app.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.models import ItemStatus, Job, JobItem, JobStatus
from app.services.ffmpeg_service import FFmpegService
from app.services.storage import StorageService
from app.services.zip_service import ZipService

logger = get_logger(__name__)


class RenderPipeline:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.storage = StorageService()
        self.ffmpeg = FFmpegService(self.storage)
        self.zip_service = ZipService(self.storage)
        self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_renders)

    async def process_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job is None:
                return
            if job.status == JobStatus.cancelled:
                return

            try:
                instructions = EditInstructions.model_validate_json(job.instructions_json or "{}")
            except Exception as exc:
                job.status = JobStatus.failed
                job.error = f"Invalid stored instructions: {exc}"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            items = db.query(JobItem).filter(JobItem.job_id == job_id).all()
            tasks = [self._process_item(job_id, item.id, instructions, job.upload_id) for item in items]
            await asyncio.gather(*tasks)

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
                job.error = "All renders failed"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            output_files = [Path(i.output_path) for i in completed if i.output_path]
            zip_path = self.zip_service.build_job_zip(job_id, output_files)
            job.zip_path = str(zip_path)
            job.status = JobStatus.completed if not failed else JobStatus.completed
            if failed:
                job.error = f"{len(failed)} of {len(items)} items failed"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Job %s completed (%d ok, %d failed)", job_id, len(completed), len(failed))
        except Exception as exc:
            logger.exception("Job %s crashed: %s", job_id, exc)
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
        instructions: EditInstructions,
        upload_id: str,
    ) -> None:
        async with self._semaphore:
            await self._render_with_retry(job_id, item_id, instructions, upload_id)

    async def _render_with_retry(
        self,
        job_id: str,
        item_id: str,
        instructions: EditInstructions,
        upload_id: str,
    ) -> None:
        settings = self.settings
        attempt = 0
        max_attempts = 1 + settings.soft_retry_count

        while attempt < max_attempts:
            attempt += 1
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
                item.progress = 0.0
                item.error = None
                item.retry_count = attempt - 1
                db.commit()

                input_path = Path(item.input_path)
                output_path = Path(item.output_path or "")

                async def on_progress(pct: float) -> None:
                    pdb = SessionLocal()
                    try:
                        it = pdb.get(JobItem, item_id)
                        if it and it.status == ItemStatus.running:
                            it.progress = pct
                            pdb.commit()
                    finally:
                        pdb.close()

                preview_dir = self.storage.output_dir(job_id) / "previews" / item_id
                result = await self.ffmpeg.render(
                    input_path=input_path,
                    output_path=output_path,
                    instructions=instructions,
                    upload_id=upload_id,
                    progress_cb=on_progress,
                    preview_dir=preview_dir,
                )

                item = db.get(JobItem, item_id)
                assert item is not None
                item.status = ItemStatus.completed
                item.progress = 100.0
                item.occurrences_replaced = result.occurrences
                item.preview_before_path = (
                    str(result.preview_before) if result.preview_before else None
                )
                item.preview_after_path = (
                    str(result.preview_after) if result.preview_after else None
                )
                if result.occurrences:
                    item.error = None
                item.finished_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(
                    "Item %s completed (attempt %d, %d occurrence(s))",
                    item_id,
                    attempt,
                    result.occurrences,
                )
                return
            except Exception as exc:
                logger.warning("Item %s attempt %d failed: %s", item_id, attempt, exc)
                item = db.get(JobItem, item_id)
                if item:
                    item.error = str(exc)
                    if attempt >= max_attempts:
                        item.status = ItemStatus.failed
                        item.finished_at = datetime.now(timezone.utc)
                    db.commit()
                if attempt >= max_attempts:
                    return
            finally:
                db.close()
            await asyncio.sleep(0.5)
