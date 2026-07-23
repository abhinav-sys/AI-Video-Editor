"""Build EditableTemplate from OCR regions + shot windows (+ optional vision)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.api.schemas.edits import EditInstructions, TextReplace
from app.api.schemas.template import (
    EditableTemplate,
    EntityRole,
    EntityStyle,
    EntityType,
    TemplateEntity,
    TrackSegment,
    VideoMeta,
)
from app.services.text_ocr import RenderRegion


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _guess_role(text: str) -> EntityRole:
    t = text.lower()
    if re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|august|july|\d{1,2}\s*&\s*\d{1,2})\b", t):
        return EntityRole.date
    if re.search(r"\$|€|£|\d+\.\d{2}|price|off\b", t):
        return EntityRole.price
    if re.search(r"\b(call|book|buy|shop|learn more|sign up)\b", t):
        return EntityRole.cta
    if re.search(r"\+?\d[\d\s\-()]{6,}", t):
        return EntityRole.phone
    # city-like single tokens often appear in hotel ads
    if re.search(r"\b(sydney|melbourne|london|paris|dubai|tokyo|new york)\b", t):
        return EntityRole.city
    return EntityRole.other


def regions_to_template(
    regions: list[RenderRegion],
    *,
    duration: float,
    width: int,
    height: int,
    fps: float = 30.0,
    logo_entities: list[TemplateEntity] | None = None,
) -> EditableTemplate:
    entities: list[TemplateEntity] = []
    for i, r in enumerate(regions):
        eid = r.entity_id or f"text_{i}_{uuid.uuid4().hex[:8]}"
        t0 = 0.0 if r.t_start is None else float(r.t_start)
        t1 = float(duration) if r.t_end is None else float(r.t_end)
        entities.append(
            TemplateEntity(
                id=eid,
                type=EntityType.text,
                role=_guess_role(r.from_text or r.ocr_text or r.text),
                text=r.text,
                track=[
                    TrackSegment(
                        t_start=t0,
                        t_end=max(t0 + 0.01, t1),
                        bbox=[r.x, r.y, r.w, r.h],
                        opacity_curve=None,
                    )
                ],
                style=EntityStyle(
                    font=r.fontfile,
                    size=r.fontsize,
                    color=_rgb_to_hex(r.font_rgb),
                    fill=_rgb_to_hex(r.fill_rgb),
                    align=r.align,
                    bold=r.bold,
                ),
                inpaint_mode="flat",
            )
        )
    if logo_entities:
        entities.extend(logo_entities)
    return EditableTemplate(
        video=VideoMeta(duration=float(duration), width=width, height=height, fps=fps),
        entities=entities,
    )


def apply_instructions_to_template(
    template: EditableTemplate,
    instructions: EditInstructions,
) -> EditableTemplate:
    """Patch entity text using find/replace pairs (by fuzzy role/text match)."""
    if not instructions.replace_text:
        return template
    entities = []
    for ent in template.entities:
        if ent.type != EntityType.text or not ent.text:
            entities.append(ent)
            continue
        new_text = ent.text
        for pair in instructions.replace_text:
            # If entity text looks like the replacement target already, keep;
            # if original from_ appears in style history we only have `text` as `to`.
            # Prefer matching from_ against role guess source stored in id/track — use pair.to when
            # pair.from_ role matches entity role loosely.
            if pair.to and (
                pair.from_.lower() in (ent.text or "").lower()
                or _guess_role(pair.from_) == ent.role
            ):
                # Only replace if from_ matches current or role aligns and text equals from_
                if pair.from_.lower() in (ent.text or "").lower():
                    new_text = (ent.text or "").replace(pair.from_, pair.to)
                elif ent.role == _guess_role(pair.from_) and len(instructions.replace_text) == 1:
                    new_text = pair.to
        entities.append(ent.model_copy(update={"text": new_text}))
    return template.model_copy(update={"entities": entities})


def template_to_regions(
    template: EditableTemplate,
    instructions: EditInstructions | None = None,
) -> list[RenderRegion]:
    """Flatten template text entities back into RenderRegion for FFmpeg."""
    patched = apply_instructions_to_template(template, instructions) if instructions else template
    regions: list[RenderRegion] = []
    for ent in patched.entities:
        if ent.type != EntityType.text or not ent.track:
            continue
        seg = ent.track[0]
        x, y, w, h = seg.bbox
        font_rgb = _hex_to_rgb(ent.style.color or "#FFFFFF")
        fill_rgb = _hex_to_rgb(ent.style.fill or "#000000")
        regions.append(
            RenderRegion(
                x=x,
                y=y,
                w=w,
                h=h,
                fill_rgb=fill_rgb,
                font_rgb=font_rgb,
                fontsize=ent.style.size or max(12, int(h * 0.85)),
                text=ent.text or "",
                align=ent.style.align or "left",
                from_text=ent.text or "",
                ocr_text=ent.text or "",
                bold=ent.style.bold,
                fontfile=ent.style.font,
                t_start=seg.t_start,
                t_end=seg.t_end,
                entity_id=ent.id,
            )
        )
    return regions


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) != 6:
        return (255, 255, 255)
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def merge_replacements_into_regions(
    regions: list[RenderRegion],
    replacements: list[TextReplace],
) -> list[RenderRegion]:
    """Ensure region.text uses `to` for matching `from` (already true in pipeline)."""
    return regions


def save_template(path: Path, template: EditableTemplate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.model_dump_json(indent=2), encoding="utf-8")
