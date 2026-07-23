"""EditableTemplate schema — video timeline entities for V2."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EntityType(str, Enum):
    text = "text"
    logo = "logo"
    graphic = "graphic"


class EntityRole(str, Enum):
    date = "date"
    city = "city"
    price = "price"
    cta = "cta"
    phone = "phone"
    logo = "logo"
    other = "other"


class TrackSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t_start: float = Field(..., ge=0)
    t_end: float = Field(..., gt=0)
    bbox: list[int] = Field(..., min_length=4, max_length=4)  # x,y,w,h
    opacity_curve: str | None = None


class EntityStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font: str | None = None
    size: int | None = None
    color: str | None = None  # #RRGGBB
    fill: str | None = None
    align: str = "left"
    bold: bool = False


class TemplateEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: EntityType = EntityType.text
    role: EntityRole = EntityRole.other
    text: str | None = None
    track: list[TrackSegment] = Field(default_factory=list)
    style: EntityStyle = Field(default_factory=EntityStyle)
    asset_filename: str | None = None
    inpaint_mode: str | None = None  # flat | telea | lama


class VideoMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration: float
    width: int
    height: int
    fps: float = 30.0


class EditableTemplate(BaseModel):
    """Canonical per-item editable timeline."""

    model_config = ConfigDict(extra="forbid")

    video: VideoMeta
    entities: list[TemplateEntity] = Field(default_factory=list)
    version: int = 1

    def to_json_schema_dict(self) -> dict[str, Any]:
        return self.model_json_schema()


class TemplatePatch(BaseModel):
    """Partial update: replace entity text/style by id."""

    model_config = ConfigDict(extra="forbid")

    entities: list[TemplateEntity] = Field(default_factory=list)
