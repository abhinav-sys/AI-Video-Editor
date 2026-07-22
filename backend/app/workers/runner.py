from __future__ import annotations

import asyncio

from app.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.models import Job, JobStatus
from app.workers.pipeline import RenderPipeline

logger = get_logger(__name__)


class JobWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.pipeline = RenderPipeline()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._active: set[str] = set()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info("Job worker started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None
        logger.info("Job worker stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._claim()
                if job_id:
                    if job_id not in self._active:
                        self._active.add(job_id)
                        asyncio.create_task(self._run_job(job_id))
                else:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(),
                            timeout=self.settings.worker_poll_interval_sec,
                        )
                    except TimeoutError:
                        pass
            except Exception:
                logger.exception("Worker loop error")
                await asyncio.sleep(2)

    def _claim(self) -> str | None:
        db = SessionLocal()
        try:
            job = (
                db.query(Job)
                .filter(Job.status == JobStatus.queued)
                .order_by(Job.created_at.asc())
                .first()
            )
            if job is None:
                return None
            job.status = JobStatus.running
            db.commit()
            return job.id
        finally:
            db.close()

    async def _run_job(self, job_id: str) -> None:
        try:
            await self.pipeline.process_job(job_id)
        finally:
            self._active.discard(job_id)


worker = JobWorker()
