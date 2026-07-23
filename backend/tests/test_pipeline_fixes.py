"""Tests for Celery heartbeat helper and jobstatus partial migration."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.core.models import JobStatus
from app.workers.tasks import _heartbeat_loop


def test_heartbeat_loop_updates_while_running():
    stop = threading.Event()
    job = MagicMock()
    job.status = JobStatus.running
    job.heartbeat_at = None
    calls: list[datetime] = []

    def _commit():
        calls.append(job.heartbeat_at)

    db = MagicMock()
    db.get.return_value = job
    db.commit.side_effect = _commit

    wait_count = {"n": 0}

    def _wait(_interval):
        wait_count["n"] += 1
        if wait_count["n"] >= 2:
            return True  # stop
        return False  # run one beat

    stop.wait = _wait  # type: ignore[method-assign]

    with patch("app.workers.tasks.SessionLocal", return_value=db):
        _heartbeat_loop("job-1", stop, lease_seconds=30)

    assert len(calls) >= 1
    assert job.heartbeat_at is not None


def test_migration_005_partial_sql_present():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "005_job_status_partial.py"
    text = path.read_text(encoding="utf-8")
    assert "ADD VALUE IF NOT EXISTS 'partial'" in text
    assert "postgresql" in text
