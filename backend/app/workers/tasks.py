"""Celery tasks wrapping existing RenderPipeline / CreatomatePipeline."""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timezone

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.models import Job, JobStatus, RenderEngine
from app.workers.creatomate_pipeline import CreatomatePipeline
from app.workers.pipeline import RenderPipeline

logger = get_logger(__name__)

try:
    from app.workers.celery_app import celery_app
except Exception:  # pragma: no cover
    celery_app = None


def _worker_id() -> str:
    return f"{socket.gethostname()}:celery"


def _claim_for_celery(job_id: str) -> str | None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return None
        if job.status == JobStatus.cancelled:
            return None
        if job.status not in (JobStatus.queued, JobStatus.running):
            return None
        now = datetime.now(timezone.utc)
        job.status = JobStatus.running
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        job.worker_id = _worker_id()
        engine = getattr(job, "engine", None) or RenderEngine.bulkcut.value
        db.commit()
        return engine
    finally:
        db.close()


def _run_job_body(job_id: str) -> str:
    engine = _claim_for_celery(job_id)
    if engine is None:
        logger.info("Celery skip job %s (not claimable)", job_id)
        return "skipped"
    try:
        if engine == RenderEngine.creatomate.value:
            asyncio.run(CreatomatePipeline().process_job(job_id))
        else:
            asyncio.run(RenderPipeline().process_job(job_id))
        return "ok"
    except Exception as exc:
        logger.exception("Celery job %s failed: %s", job_id, exc)
        db = SessionLocal()
        try:
            job = db.get(Job, job_id)
            if job and job.status == JobStatus.running:
                job.status = JobStatus.failed
                job.error = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
        raise


if celery_app is not None:

    @celery_app.task(name="app.workers.tasks.process_job", bind=True, max_retries=1)
    def process_job(self, job_id: str) -> str:  # noqa: ARG001
        return _run_job_body(job_id)

else:

    def process_job(job_id: str) -> str:  # type: ignore[misc]
        return _run_job_body(job_id)


def enqueue_job(job_id: str) -> None:
    """Enqueue a Celery task when not using the inline worker."""
    if celery_app is None:
        raise RuntimeError(
            "Celery is not installed. pip install 'celery[redis]' or set USE_INLINE_WORKER=true"
        )
    process_job.delay(job_id)  # type: ignore[attr-defined]
