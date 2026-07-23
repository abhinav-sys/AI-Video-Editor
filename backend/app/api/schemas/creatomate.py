from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.schemas.edits import EditInstructions


class CreatomateInstructions(BaseModel):
    """Instructions for the Creatomate engine.

    - edit: upload a video → OCR builds a RenderScript template → replace text
    - template / source: legacy Quick Promo / bare RenderScript modes
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["edit", "template", "source"] = "edit"
    template_id: str | None = None
    modifications: dict[str, Any] = Field(default_factory=dict)
    text_primary: str = ""
    text_secondary: str = ""
    video_urls: list[str] = Field(default_factory=list)
    edits: EditInstructions | None = None

    @model_validator(mode="after")
    def require_content(self) -> CreatomateInstructions:
        if self.mode == "edit":
            if self.edits is None:
                raise ValueError("Creatomate edit mode requires parsed edit instructions")
            return self
        if self.mode == "template" and not (self.template_id or "").strip():
            raise ValueError("Creatomate template mode requires template_id")
        if self.mode != "edit" and not self.text_primary.strip() and not self.modifications:
            raise ValueError("Creatomate job needs text or modifications")
        return self


def prompt_to_creatomate_texts(prompt: str) -> tuple[str, str]:
    """Split a natural-language prompt into Text-1 / Text-2 for Quick Promo."""
    cleaned = " ".join(prompt.strip().split())
    if not cleaned:
        return "Your Text Here", "Created with Creatomate"

    if "\n" in prompt:
        parts = [p.strip() for p in prompt.splitlines() if p.strip()]
        if len(parts) >= 2:
            return parts[0][:120], parts[1][:120]
    if " / " in cleaned:
        left, right = cleaned.split(" / ", 1)
        return left[:120], right[:120]

    lower = cleaned.lower()
    if " to " in lower and "replace" in lower:
        idx = lower.rfind(" to ")
        candidate = cleaned[idx + 4 :].strip(" .,")
        if candidate:
            return candidate[:120], "Updated via Creatomate API"

    if len(cleaned) <= 48:
        return cleaned, "Direct API render"
    return cleaned[:80], cleaned[80:160] or "Direct API render"
