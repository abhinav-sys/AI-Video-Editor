from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import ItemStatus, JobStatus


class JobCreateRequest(BaseModel):
    upload_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1, max_length=4000)


class JobItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    status: ItemStatus
    progress: float
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatus
    prompt: str
    instructions_json: str | None = None
    upload_id: str
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
