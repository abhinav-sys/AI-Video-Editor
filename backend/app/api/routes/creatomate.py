from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import require_api_key
from app.services.creatomate_service import CreatomateError, CreatomateService

router = APIRouter(
    prefix="/creatomate",
    tags=["creatomate"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/templates")
async def list_templates() -> dict:
    client = CreatomateService()
    if not client.configured:
        raise HTTPException(status_code=503, detail="CREATOMATE_API_KEY is not configured")
    try:
        templates = await client.list_templates()
    except CreatomateError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "configured": True,
        "default_template_id": client.settings.creatomate_default_template_id or None,
        "templates": [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "tags": t.get("tags") or [],
                "updated_at": t.get("updated_at"),
            }
            for t in templates
        ],
    }
