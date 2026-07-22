from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.llm.factory import get_llm_provider
from app.services.ffmpeg_service import FFmpegService

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    ffmpeg_ok = await FFmpegService().check_available()
    llm = get_llm_provider()
    llm_ok = await llm.health_check()
    status = "ok" if ffmpeg_ok else "degraded"
    return {
        "status": status,
        "ffmpeg": ffmpeg_ok,
        "llm_provider": llm.name,
        "llm_ok": llm_ok,
        "model": settings.ollama_model,
        "max_concurrent_renders": settings.max_concurrent_renders,
    }
