"""Celery application for durable render workers (optional dependency)."""

from __future__ import annotations

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

try:
    from celery import Celery

    celery_app = Celery(
        "ai_video_editor",
        broker=settings.broker_url,
        backend=settings.result_backend,
        include=["app.workers.tasks"],
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
    )
except ImportError:  # pragma: no cover
    celery_app = None  # type: ignore
    logger.warning("celery not installed — durable worker unavailable")
