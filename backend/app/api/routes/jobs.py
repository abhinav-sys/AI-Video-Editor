from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.api.schemas.creatomate import CreatomateInstructions, prompt_to_creatomate_texts
from app.api.schemas.jobs import JobCreateRequest, JobCreateResponse, JobResponse
from app.config import get_settings
from app.core.logging import get_logger
from app.core.models import Asset, AssetKind, RenderEngine
from app.llm.factory import get_llm_provider
from app.services.creatomate_service import CreatomateService
from app.services.job_service import JobService

logger = get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=JobCreateResponse)
async def create_job(
    body: JobCreateRequest,
    db: Session = Depends(get_db),
) -> JobCreateResponse:
    service = JobService(db)
    settings = get_settings()
    engine = (body.engine or RenderEngine.bulkcut.value).lower()

    if engine == RenderEngine.creatomate.value:
        creatomate = CreatomateService()
        if not creatomate.configured:
            raise HTTPException(
                status_code=503,
                detail="Creatomate is not configured — set CREATOMATE_API_KEY in backend/.env",
            )

        mode = body.creatomate_mode if body.creatomate_mode in ("edit", "template", "source") else "edit"

        # --- Primary path: upload video → OCR template → Creatomate render ---
        if mode == "edit":
            if not body.upload_id:
                raise HTTPException(
                    status_code=422,
                    detail="Upload a video first — Creatomate edit mode builds a template from your clip",
                )
            assets = (
                db.query(Asset)
                .filter(Asset.upload_id == body.upload_id, Asset.kind != AssetKind.video)
                .all()
            )
            llm = get_llm_provider()
            try:
                edits = await llm.parse_prompt(body.prompt, [a.filename for a in assets])
            except Exception as exc:
                logger.warning("LLM parse failed (creatomate edit): %s", exc)
                raise HTTPException(
                    status_code=422, detail=f"Failed to parse prompt: {exc}"
                ) from exc

            try:
                instructions = CreatomateInstructions(mode="edit", edits=edits)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            try:
                job = service.create_creatomate_job(
                    body.prompt,
                    instructions.model_dump_json(by_alias=True),
                    upload_id=body.upload_id,
                    require_videos=True,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            return JobCreateResponse(id=job.id, status=job.status, engine=job.engine)

        # --- Legacy Quick Promo / bare source modes ---
        primary, secondary = prompt_to_creatomate_texts(body.prompt)
        try:
            assets = []
            if body.upload_id:
                assets = (
                    db.query(Asset)
                    .filter(Asset.upload_id == body.upload_id, Asset.kind != AssetKind.video)
                    .all()
                )
            llm = get_llm_provider()
            parsed = await llm.parse_prompt(body.prompt, [a.filename for a in assets])
            if parsed.replace_text:
                tos = [t.to for t in parsed.replace_text if t.to]
                if tos:
                    primary = tos[0]
                if len(tos) >= 2:
                    secondary = tos[1]
                elif parsed.replace_text:
                    secondary = f"{parsed.replace_text[0].from_} → {parsed.replace_text[0].to}"
        except Exception as exc:
            logger.info("Creatomate prompt parse fallback to raw split: %s", exc)

        template_id = (
            (body.template_id or "").strip()
            or settings.creatomate_default_template_id
            or None
        )
        if mode == "template" and not template_id:
            raise HTTPException(
                status_code=422,
                detail="Creatomate template mode needs template_id (or CREATOMATE_DEFAULT_TEMPLATE_ID)",
            )

        mods: dict = {"Text-1": primary, "Text-2": secondary}
        video_urls: list[str] = []
        if body.video_url:
            mods["Video"] = body.video_url.strip()
            video_urls.append(body.video_url.strip())

        try:
            instructions = CreatomateInstructions(
                mode=mode,  # type: ignore[arg-type]
                template_id=template_id,
                modifications=mods,
                text_primary=primary,
                text_secondary=secondary,
                video_urls=video_urls,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            job = service.create_creatomate_job(
                body.prompt,
                instructions.model_dump_json(),
                upload_id=body.upload_id,
                require_videos=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return JobCreateResponse(id=job.id, status=job.status, engine=job.engine)

    # --- Bulkcut (local FFmpeg / OCR) ---
    if not body.upload_id:
        raise HTTPException(status_code=422, detail="upload_id is required for Bulkcut jobs")

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
        job = service.create_job(
            body.upload_id,
            body.prompt,
            instructions,
            engine=RenderEngine.bulkcut.value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JobCreateResponse(id=job.id, status=job.status, engine=job.engine)


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
