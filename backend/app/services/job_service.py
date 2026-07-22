from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, joinedload

from app.api.schemas.edits import EditInstructions
from app.api.schemas.jobs import JobItemResponse, JobResponse
from app.core.logging import get_logger
from app.core.models import Asset, AssetKind, ItemStatus, Job, JobItem, JobStatus, UploadBatch
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
        logger.info("Created job %s with %d videos", job.id, len(video_assets))
        return job

    def get_job(self, job_id: str) -> Job | None:
        return (
            self.db.query(Job)
            .options(joinedload(Job.items), joinedload(Job.assets))
            .filter(Job.id == job_id)
            .first()
        )

    def to_response(self, job: Job) -> JobResponse:
        items = [JobItemResponse.model_validate(i) for i in job.items]
        progress = 0.0
        if items:
            progress = sum(i.progress for i in items) / len(items)
        return JobResponse(
            id=job.id,
            status=job.status,
            prompt=job.prompt,
            instructions_json=job.instructions_json,
            upload_id=job.upload_id,
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
