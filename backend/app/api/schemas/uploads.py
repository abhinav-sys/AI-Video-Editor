from __future__ import annotations

from pydantic import BaseModel, Field


class UploadedFileInfo(BaseModel):
    filename: str
    kind: str
    size_bytes: int


class UploadResponse(BaseModel):
    upload_id: str
    videos: list[UploadedFileInfo] = Field(default_factory=list)
    assets: list[UploadedFileInfo] = Field(default_factory=list)
