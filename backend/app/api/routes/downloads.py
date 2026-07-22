from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.core.models import Job, JobStatus
from app.services.job_service import JobService

router = APIRouter(tags=["downloads"], dependencies=[Depends(require_api_key)])


@router.get("/jobs/{job_id}/download")
def download_job_zip(job_id: str, db: Session = Depends(get_db)) -> FileResponse:
    service = JobService(db)
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.completed or not job.zip_path:
        raise HTTPException(status_code=409, detail="ZIP not ready")
    path = Path(job.zip_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="ZIP file missing")
    return FileResponse(
        path=path,
        media_type="application/zip",
        filename=f"job-{job_id}.zip",
    )
