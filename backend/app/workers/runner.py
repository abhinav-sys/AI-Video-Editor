"""Job worker: inline asyncio poller (dev) or Celery enqueue + lease reconciler."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.models import Job, JobStatus, RenderEngine
from app.workers.creatomate_pipeline import CreatomatePipeline
from app.workers.pipeline import RenderPipeline

logger = get_logger(__name__)


class JobWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.pipeline = RenderPipeline()
        self.creatomate_pipeline = CreatomatePipeline()
        self._task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._active: set[str] = set()

    async def start(self) -> None:
        self._stop.clear()
        if self._reconcile_task is None or self._reconcile_task.done():
            self._reconcile_task = asyncio.create_task(
                self._reconcile_loop(), name="job-reconciler"
            )
        if not self.settings.use_inline_worker:
            logger.info("Inline job worker disabled (USE_INLINE_WORKER=false); Celery owns renders")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info("Inline job worker started")

    async def stop(self) -> None:
        self._stop.set()
        for t in (self._task, self._reconcile_task):
            if t:
                await t
        self._task = None
        self._reconcile_task = None
        logger.info("Job worker stopped")

    async def _reconcile_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile_stale()
            except Exception:
                logger.exception("Job reconcile error")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.job_reconcile_interval_sec,
                )
            except TimeoutError:
                pass

    def _reconcile_stale(self) -> None:
        """Requeue jobs stuck in running without a recent heartbeat."""
        lease = timedelta(seconds=max(30, self.settings.job_lease_seconds))
        cutoff = datetime.now(timezone.utc) - lease
        db = SessionLocal()
        try:
            stuck = (
                db.query(Job)
                .filter(Job.status == JobStatus.running)
                .all()
            )
            for job in stuck:
                hb = job.heartbeat_at or job.started_at or job.updated_at
                if hb is None:
                    continue
                # Make naive/aware comparable
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if hb < cutoff:
                    logger.warning("Requeuing stale job %s (last heartbeat %s)", job.id, hb)
                    job.status = JobStatus.queued
                    job.worker_id = None
                    job.error = (job.error or "") + " | requeued after stale lease"
                    db.commit()
                    if not self.settings.use_inline_worker:
                        try:
                            from app.workers.tasks import enqueue_job

                            enqueue_job(job.id)
                        except Exception:
                            logger.exception("Failed to re-enqueue stale job %s", job.id)
        finally:
            db.close()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                claimed = self._claim()
                if claimed:
                    job_id, engine = claimed
                    if job_id not in self._active:
                        self._active.add(job_id)
                        asyncio.create_task(self._run_job(job_id, engine))
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

    def _claim(self) -> tuple[str, str] | None:
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
            engine = getattr(job, "engine", None) or RenderEngine.bulkcut.value
            now = datetime.now(timezone.utc)
            job.status = JobStatus.running
            job.started_at = now
            job.heartbeat_at = now
            job.worker_id = "inline"
            db.commit()
            return job.id, engine
        finally:
            db.close()

    async def _run_job(self, job_id: str, engine: str) -> None:
        try:
            # Heartbeat while running
            async def _beat() -> None:
                while True:
                    await asyncio.sleep(max(10.0, self.settings.job_lease_seconds / 3))
                    db = SessionLocal()
                    try:
                        job = db.get(Job, job_id)
                        if job is None or job.status != JobStatus.running:
                            return
                        job.heartbeat_at = datetime.now(timezone.utc)
                        db.commit()
                    finally:
                        db.close()

            beat = asyncio.create_task(_beat())
            try:
                if engine == RenderEngine.creatomate.value:
                    await self.creatomate_pipeline.process_job(job_id)
                else:
                    await self.pipeline.process_job(job_id)
            finally:
                beat.cancel()
        finally:
            self._active.discard(job_id)


worker = JobWorker()


def dispatch_job(job_id: str) -> None:
    """Called after create: enqueue Celery when inline worker is off."""
    settings = get_settings()
    if settings.use_inline_worker:
        return
    from app.workers.tasks import enqueue_job

    enqueue_job(job_id)
