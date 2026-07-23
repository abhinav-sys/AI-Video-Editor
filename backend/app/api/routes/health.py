from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.llm.factory import get_llm_provider
from app.services.creatomate_service import CreatomateService
from app.services.ffmpeg_service import FFmpegService

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    ffmpeg_ok = await FFmpegService().check_available()
    llm = get_llm_provider()
    llm_ok = await llm.health_check()
    creatomate = CreatomateService()
    creatomate_ok = await creatomate.health_check() if creatomate.configured else False
    status = "ok" if ffmpeg_ok or creatomate_ok else "degraded"
    return {
        "status": status,
        "ffmpeg": ffmpeg_ok,
        "llm_provider": llm.name,
        "llm_ok": llm_ok,
        "model": settings.ollama_model,
        "max_concurrent_renders": settings.max_concurrent_renders,
        "creatomate": creatomate.configured,
        "creatomate_ok": creatomate_ok,
        "creatomate_template_id": settings.creatomate_default_template_id or None,
    }
