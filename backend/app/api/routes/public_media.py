from __future__ import annotations

import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(tags=["public-media"])

# token -> (absolute path, expires_at epoch)
_MEDIA_TOKENS: dict[str, tuple[str, float]] = {}
_TTL_SEC = 60 * 60 * 6  # 6 hours


def register_public_media(path: Path, *, ttl_sec: int = _TTL_SEC) -> str:
    """Register a local file for temporary unauthenticated fetch; return absolute URL."""
    settings = get_settings()
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    token = secrets.token_urlsafe(24)
    _MEDIA_TOKENS[token] = (str(path.resolve()), time.time() + ttl_sec)
    return f"{base}/public-media/{token}"


def public_media_available() -> bool:
    return bool(get_settings().public_base_url.strip())


@router.get("/public-media/{token}")
def fetch_public_media(token: str) -> FileResponse:
    entry = _MEDIA_TOKENS.get(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown or expired media token")
    path_str, expires = entry
    if time.time() > expires:
        _MEDIA_TOKENS.pop(token, None)
        raise HTTPException(status_code=404, detail="Media token expired")
    path = Path(path_str)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Media file missing")
    media = "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"
    return FileResponse(path=path, media_type=media, filename=path.name)
