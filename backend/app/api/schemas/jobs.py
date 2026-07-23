from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import ItemStatus, JobStatus


class JobCreateRequest(BaseModel):
    upload_id: str | None = None
    prompt: str = Field(..., min_length=1, max_length=4000)
    engine: str = Field(default="bulkcut", pattern="^(bulkcut|creatomate)$")
    template_id: str | None = Field(default=None, max_length=64)
    creatomate_mode: str = Field(default="edit", pattern="^(edit|template|source)$")
    video_url: str | None = Field(
        default=None,
        max_length=2048,
        description="Optional public video URL for Creatomate Video modification",
    )


class JobItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    status: ItemStatus
    progress: float
    error: str | None = None
    occurrences_replaced: int | None = None
    preview_before_url: str | None = None
    preview_after_url: str | None = None
    has_template: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    prompt: str
    instructions_json: str | None = None
    upload_id: str
    engine: str = "bulkcut"
    error: str | None = None
    progress: float = 0.0
    items: list[JobItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    download_ready: bool = False


class JobCreateResponse(BaseModel):
    id: str
    status: JobStatus
    engine: str = "bulkcut"
