from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.api.schemas.jobs import JobCreateRequest, JobCreateResponse, JobResponse
from app.core.logging import get_logger
from app.core.models import Asset, AssetKind
from app.llm.factory import get_llm_provider
from app.services.job_service import JobService

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=JobCreateResponse)
async def create_job(
    body: JobCreateRequest,
    db: Session = Depends(get_db),
) -> JobCreateResponse:
    service = JobService(db)

    assets = (
        db.query(Asset)
        .filter(Asset.upload_id == body.upload_id, Asset.kind != AssetKind.video)
        .all()
    )
    asset_names = [a.filename for a in assets]

    llm = get_llm_provider()
    try:
        instructions = await llm.parse_prompt(body.prompt, asset_names)
    except Exception as exc:
        logger.warning("LLM parse failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Failed to parse prompt: {exc}") from exc

    try:
        job = service.create_job(body.upload_id, body.prompt, instructions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JobCreateResponse(id=job.id, status=job.status)


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobResponse:
    service = JobService(db)
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return service.to_response(job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, db: Session = Depends(get_db)) -> JobResponse:
    service = JobService(db)
    try:
        job = service.cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return service.to_response(job)
