from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app.api.schemas.edits import EditInstructions
from app.api.schemas.jobs import JobItemResponse, JobResponse
from app.core.logging import get_logger
from app.core.models import Asset, AssetKind, ItemStatus, Job, JobItem, JobStatus, RenderEngine, UploadBatch
from app.services.storage import StorageService

logger = get_logger(__name__)


class JobService:
    def __init__(self, db: Session, storage: StorageService | None = None) -> None:
        self.db = db
        self.storage = storage or StorageService()

    def create_job(
        self,
        upload_id: str,
        prompt: str,
        instructions: EditInstructions,
        *,
        engine: str = RenderEngine.bulkcut.value,
    ) -> Job:
        batch = self.db.get(UploadBatch, upload_id)
        if batch is None:
            raise ValueError("Unknown upload_id")

        video_assets = (
            self.db.query(Asset)
            .filter(Asset.upload_id == upload_id, Asset.kind == AssetKind.video)
            .all()
        )
        if not video_assets:
            raise ValueError("Upload has no videos")

        # validate asset references
        non_video = (
            self.db.query(Asset)
            .filter(Asset.upload_id == upload_id, Asset.kind != AssetKind.video)
            .all()
        )
        names = {a.filename for a in non_video}
        if instructions.replace_logo and instructions.replace_logo not in names:
            # allow basename match
            if not any(a.filename.endswith(instructions.replace_logo) or a.filename == instructions.replace_logo for a in non_video):
                resolved = self.storage.resolve_asset(upload_id, instructions.replace_logo)
                if resolved is None:
                    raise ValueError(f"Logo asset not found: {instructions.replace_logo}")

        job = Job(
            status=JobStatus.queued,
            prompt=prompt,
            instructions_json=instructions.model_dump_json(by_alias=True),
            upload_id=upload_id,
            engine=engine,
        )
        self.db.add(job)
        self.db.flush()

        for asset in (
            self.db.query(Asset).filter(Asset.upload_id == upload_id).all()
        ):
            asset.job_id = job.id

        out_dir = self.storage.output_dir(job.id)
        for video in video_assets:
            stem = Path(video.filename).stem
            out_path = out_dir / f"{stem}_edited.mp4"
            item = JobItem(
                job_id=job.id,
                input_path=video.path,
                output_path=str(out_path),
                original_filename=video.filename,
                status=ItemStatus.pending,
                progress=0.0,
            )
            self.db.add(item)

        self.db.commit()
        self.db.refresh(job)
        logger.info("Created job %s with %d videos (engine=%s)", job.id, len(video_assets), engine)
        return job

    def create_creatomate_job(
        self,
        prompt: str,
        instructions_json: str,
        *,
        upload_id: str | None = None,
        require_videos: bool = True,
    ) -> Job:
        """Create a Creatomate job. Edit mode requires uploaded videos."""
        if upload_id:
            batch = self.db.get(UploadBatch, upload_id)
            if batch is None:
                raise ValueError("Unknown upload_id")
        else:
            if require_videos:
                raise ValueError("Upload a video first for Creatomate edit mode")
            batch = UploadBatch()
            self.db.add(batch)
            self.db.flush()
            upload_id = batch.id

        video_assets = (
            self.db.query(Asset)
            .filter(Asset.upload_id == upload_id, Asset.kind == AssetKind.video)
            .all()
        )
        if require_videos and not video_assets:
            raise ValueError("Upload has no videos — Creatomate edit needs a source clip")

        job = Job(
            status=JobStatus.queued,
            prompt=prompt,
            instructions_json=instructions_json,
            upload_id=upload_id,
            engine=RenderEngine.creatomate.value,
        )
        self.db.add(job)
        self.db.flush()

        for asset in self.db.query(Asset).filter(Asset.upload_id == upload_id).all():
            asset.job_id = job.id

        out_dir = self.storage.output_dir(job.id)
        if video_assets:
            for video in video_assets:
                stem = Path(video.filename).stem
                out_path = out_dir / f"{stem}_creatomate.mp4"
                self.db.add(
                    JobItem(
                        job_id=job.id,
                        input_path=video.path,
                        output_path=str(out_path),
                        original_filename=video.filename,
                        status=ItemStatus.pending,
                        progress=0.0,
                    )
                )
        else:
            out_path = out_dir / "creatomate_render.mp4"
            self.db.add(
                JobItem(
                    job_id=job.id,
                    input_path="creatomate://direct",
                    output_path=str(out_path),
                    original_filename="creatomate_render.mp4",
                    status=ItemStatus.pending,
                    progress=0.0,
                )
            )

        self.db.commit()
        self.db.refresh(job)
        logger.info(
            "Created Creatomate job %s (%d video source(s))",
            job.id,
            len(video_assets),
        )
        return job

    def get_job(self, job_id: str) -> Job | None:
        return (
            self.db.query(Job)
            .options(joinedload(Job.items), joinedload(Job.assets))
            .filter(Job.id == job_id)
            .first()
        )

    def to_response(self, job: Job) -> JobResponse:
        items: list[JobItemResponse] = []
        for i in job.items:
            items.append(
                JobItemResponse(
                    id=i.id,
                    original_filename=i.original_filename,
                    status=i.status,
                    progress=i.progress,
                    error=i.error,
                    occurrences_replaced=i.occurrences_replaced,
                    preview_before_url=(
                        f"/jobs/{job.id}/items/{i.id}/preview/before"
                        if i.preview_before_path
                        else None
                    ),
                    preview_after_url=(
                        f"/jobs/{job.id}/items/{i.id}/preview/after"
                        if i.preview_after_path
                        else None
                    ),
                    started_at=i.started_at,
                    finished_at=i.finished_at,
                )
            )
        progress = 0.0
        if items:
            progress = sum(i.progress for i in items) / len(items)
        return JobResponse(
            id=job.id,
            status=job.status,
            prompt=job.prompt,
            instructions_json=job.instructions_json,
            upload_id=job.upload_id,
            engine=getattr(job, "engine", RenderEngine.bulkcut.value) or RenderEngine.bulkcut.value,
            error=job.error,
            progress=round(progress, 2),
            items=items,
            created_at=job.created_at,
            updated_at=job.updated_at,
            completed_at=job.completed_at,
            download_ready=job.status == JobStatus.completed and bool(job.zip_path),
        )

    def cancel_job(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        if job is None:
            raise ValueError("Job not found")
        if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
            return job
        job.status = JobStatus.cancelled
        job.completed_at = datetime.now(timezone.utc)
        for item in job.items:
            if item.status in (ItemStatus.pending, ItemStatus.running):
                item.status = ItemStatus.cancelled
                item.finished_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(job)
        return job

    def claim_next_queued(self) -> Job | None:
        job = (
            self.db.query(Job)
            .filter(Job.status == JobStatus.queued)
            .order_by(Job.created_at.asc())
            .first()
        )
        if job is None:
            return None
        job.status = JobStatus.running
        self.db.commit()
        self.db.refresh(job)
        return job
