"""Template inspect / patch / re-render routes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.api.schemas.edits import EditInstructions
from app.api.schemas.template import EditableTemplate, TemplatePatch
from app.core.models import ItemStatus, JobItem, JobStatus
from app.services.ffmpeg_service import FFmpegService
from app.services.job_service import JobService
from app.services.storage import StorageService
from app.services.timeline_service import template_to_regions

router = APIRouter(prefix="/jobs", tags=["templates"], dependencies=[Depends(require_api_key)])


@router.get("/{job_id}/items/{item_id}/template", response_model=EditableTemplate)
def get_item_template(job_id: str, item_id: str, db: Session = Depends(get_db)) -> EditableTemplate:
    item = _get_item(db, job_id, item_id)
    if not item.template_json:
        raise HTTPException(status_code=404, detail="Template not available yet")
    try:
        return EditableTemplate.model_validate_json(item.template_json)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt template: {exc}") from exc


@router.patch("/{job_id}/items/{item_id}/template", response_model=EditableTemplate)
def patch_item_template(
    job_id: str,
    item_id: str,
    body: TemplatePatch,
    db: Session = Depends(get_db),
) -> EditableTemplate:
    item = _get_item(db, job_id, item_id)
    if not item.template_json:
        raise HTTPException(status_code=404, detail="Template not available yet")
    current = EditableTemplate.model_validate_json(item.template_json)
    by_id = {e.id: e for e in body.entities}
    merged = []
    for ent in current.entities:
        if ent.id in by_id:
            merged.append(by_id[ent.id])
        else:
            merged.append(ent)
    # Allow adding new entities from patch
    existing = {e.id for e in current.entities}
    for ent in body.entities:
        if ent.id not in existing:
            merged.append(ent)
    updated = current.model_copy(update={"entities": merged})
    item.template_json = updated.model_dump_json()
    db.commit()
    return updated


@router.post("/{job_id}/items/{item_id}/rerender")
async def rerender_from_template(
    job_id: str,
    item_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """Re-render a single item from its stored EditableTemplate."""
    service = JobService(db)
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    item = _get_item(db, job_id, item_id)
    if not item.template_json:
        raise HTTPException(status_code=404, detail="Template not available yet")
    template = EditableTemplate.model_validate_json(item.template_json)
    try:
        instructions = EditInstructions.model_validate_json(job.instructions_json or "{}")
    except Exception:
        instructions = EditInstructions(replace_text=[])

    regions = template_to_regions(template, instructions)
    storage = StorageService()
    ffmpeg = FFmpegService(storage)
    out = Path(item.output_path) if item.output_path else storage.output_dir(job_id) / f"{item.id}_edited.mp4"
    preview_dir = storage.output_dir(job_id) / item.id
    item.status = ItemStatus.running
    item.progress = 5.0
    item.error = None
    db.commit()

    try:
        result = await ffmpeg.render(
            Path(item.input_path),
            out,
            instructions,
            job.upload_id,
            preview_dir=preview_dir,
            text_regions=regions,
        )
        item.output_path = str(out)
        item.status = ItemStatus.completed
        item.progress = 100.0
        item.occurrences_replaced = result.occurrences
        item.preview_before_path = str(result.preview_before) if result.preview_before else None
        item.preview_after_path = str(result.preview_after) if result.preview_after else None
        item.finished_at = datetime.now(timezone.utc)
        # Refresh zip
        from app.services.zip_service import ZipService

        completed = [i for i in job.items if i.status == ItemStatus.completed or i.id == item.id]
        # reload
        db.refresh(item)
        items = db.query(JobItem).filter(JobItem.job_id == job_id, JobItem.status == ItemStatus.completed).all()
        files = [Path(i.output_path) for i in items if i.output_path]
        if item.status == ItemStatus.completed and out not in files:
            files.append(out)
        zip_path = ZipService(storage).build_job_zip(job_id, files)
        job.zip_path = str(zip_path)
        if job.status in (JobStatus.failed, JobStatus.cancelled):
            pass
        else:
            job.status = JobStatus.completed
        db.commit()
        return {"ok": True, "output_path": str(out), "occurrences": result.occurrences}
    except Exception as exc:
        item.status = ItemStatus.failed
        item.error = str(exc)
        item.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _get_item(db: Session, job_id: str, item_id: str) -> JobItem:
    item = db.get(JobItem, item_id)
    if item is None or item.job_id != job_id:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
