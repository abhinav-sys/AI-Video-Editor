"""Open-vocabulary / heuristic logo & graphic detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import get_settings
from app.core.logging import get_logger
from app.api.schemas.template import (
    EntityRole,
    EntityStyle,
    EntityType,
    TemplateEntity,
    TrackSegment,
)

logger = get_logger(__name__)


@dataclass
class VisionHit:
    label: str
    bbox: tuple[int, int, int, int]
    score: float


def detect_logos_and_graphics(
    frame_path: Path,
    *,
    t_start: float = 0.0,
    t_end: float = 10.0,
) -> list[TemplateEntity]:
    if not get_settings().enable_vision_detect:
        return []
    hits = _try_grounding_dino(frame_path)
    if not hits:
        hits = _heuristic_logo_regions(frame_path)
    entities: list[TemplateEntity] = []
    for i, hit in enumerate(hits):
        x, y, w, h = hit.bbox
        role = EntityRole.logo if "logo" in hit.label.lower() else EntityRole.cta
        etype = EntityType.logo if role == EntityRole.logo else EntityType.graphic
        entities.append(
            TemplateEntity(
                id=f"{etype.value}_{i}",
                type=etype,
                role=role,
                text=None,
                track=[
                    TrackSegment(
                        t_start=t_start,
                        t_end=t_end,
                        bbox=[x, y, w, h],
                    )
                ],
                style=EntityStyle(),
            )
        )
    return entities


def _try_grounding_dino(frame_path: Path) -> list[VisionHit]:
    """Optional GroundingDINO / transformers pipeline."""
    try:
        # Soft dependency — skip quietly when not installed
        import torch  # noqa: F401
        from transformers import pipeline  # type: ignore
    except Exception:
        return []

    try:
        # Zero-shot object detection if available
        detector = pipeline(
            model="IDEA-Research/grounding-dino-tiny",
            task="zero-shot-object-detection",
        )
        results = detector(
            str(frame_path),
            candidate_labels=["logo", "brand mark", "call to action button"],
        )
        hits: list[VisionHit] = []
        for r in results or []:
            box = r.get("box") or {}
            xmin = int(box.get("xmin", 0))
            ymin = int(box.get("ymin", 0))
            xmax = int(box.get("xmax", 0))
            ymax = int(box.get("ymax", 0))
            hits.append(
                VisionHit(
                    label=str(r.get("label", "logo")),
                    bbox=(xmin, ymin, max(1, xmax - xmin), max(1, ymax - ymin)),
                    score=float(r.get("score", 0.0)),
                )
            )
        return hits
    except Exception as exc:
        logger.info("GroundingDINO unavailable (%s); using heuristic detector", exc)
        return []


def _heuristic_logo_regions(frame_path: Path) -> list[VisionHit]:
    """Contour-based compact regions in corners — weak but dependency-free."""
    try:
        import cv2
    except ImportError:
        return []

    img = cv2.imread(str(frame_path))
    if img is None:
        return []
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hits: list[VisionHit] = []
    area_img = float(h * w)
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        area = bw * bh
        if area < area_img * 0.002 or area > area_img * 0.08:
            continue
        aspect = bw / max(1, bh)
        if aspect < 0.4 or aspect > 3.5:
            continue
        # Prefer upper corners for logos
        if y > h * 0.45:
            continue
        if x > w * 0.55 and x + bw < w * 0.95 or x < w * 0.45:
            hits.append(VisionHit(label="logo", bbox=(x, y, bw, bh), score=0.4))
    hits.sort(key=lambda z: z.score * (z.bbox[2] * z.bbox[3]), reverse=True)
    return hits[:3]
