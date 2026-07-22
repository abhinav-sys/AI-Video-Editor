from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WatermarkPosition(str, Enum):
    top_left = "top-left"
    top_right = "top-right"
    bottom_left = "bottom-left"
    bottom_right = "bottom-right"
    center = "center"


class TextReplace(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=0)

    @field_validator("from_", "to")
    @classmethod
    def strip_values(cls, v: str) -> str:
        return v.strip()


class EditInstructions(BaseModel):
    """Canonical edit contract emitted by the LLM and validated before FFmpeg."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    replace_text: list[TextReplace] = Field(default_factory=list)
    replace_logo: str | None = None
    watermark: WatermarkPosition | None = None
    watermark_image: str | None = Field(
        default=None,
        description="Optional image filename for watermark overlay; defaults to logo if set.",
    )

    @field_validator("replace_logo", "watermark_image")
    @classmethod
    def normalize_filename(cls, v: str | None) -> str | None:
        if v is None:
            return None
        name = v.strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("Asset filename must be a plain basename")
        return name

    @model_validator(mode="after")
    def reject_noop(self) -> EditInstructions:
        if not self.replace_text and not self.replace_logo and self.watermark is None:
            raise ValueError("Edit instructions must contain at least one operation")
        return self


class ParseResult(BaseModel):
    instructions: EditInstructions
    raw: str
