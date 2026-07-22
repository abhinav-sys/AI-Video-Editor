from __future__ import annotations

"""Locate burned-in text lines inside lower-third banners (no OCR required)."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class TextRegion:
    x: int
    y: int
    w: int
    h: int
    fill_rgb: tuple[int, int, int]
    fontsize: int


def _luma(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_banner_pixel(r: int, g: int, b: int) -> bool:
    """Dark teal / navy fill typical of event banners."""
    lum = _luma(r, g, b)
    return lum < 90 and b >= r and g >= r - 10 and (g + b) > 60


def locate_lower_banner_text_lines(frame_path: Path, max_lines: int = 3) -> list[TextRegion]:
    """
    Find bright-on-dark text rows inside the dominant lower-third banner.

    Returns lines top→bottom. Callers usually want the bottom line for date swaps.
    """
    im = Image.open(frame_path).convert("RGB")
    width, height = im.size
    y0 = int(height * 0.52)
    y1 = int(height * 0.92)

    # Row scores: banner darkness + bright glyph hits
    bright_cols: list[list[int]] = []
    banner_score = [0] * height
    for y in range(y0, y1):
        cols: list[int] = []
        dark = 0
        for x in range(int(width * 0.05), int(width * 0.95), 2):
            r, g, b = im.getpixel((x, y))
            if _is_banner_pixel(r, g, b):
                dark += 1
            if _luma(r, g, b) > 145:
                cols.append(x)
        bright_cols.append(cols)
        banner_score[y] = dark

    # Banner vertical span: dense dark rows
    threshold = max(40, int((width * 0.95 - width * 0.05) / 2 * 0.35))
    banner_ys = [y for y in range(y0, y1) if banner_score[y] >= threshold]
    if len(banner_ys) < 20:
        return []

    by0, by1 = min(banner_ys), max(banner_ys)

    # Group consecutive bright rows into text lines inside the banner
    lines: list[tuple[int, int, int, int]] = []  # y0,y1,x0,x1
    y = by0
    idx_base = y0
    while y <= by1:
        cols = bright_cols[y - idx_base] if y0 <= y < y1 else []
        if len(cols) >= 25:
            y_start = y
            xs: list[int] = []
            while y <= by1 and len(bright_cols[y - idx_base]) >= 18:
                xs.extend(bright_cols[y - idx_base])
                y += 1
            y_end = y - 1
            if xs and (y_end - y_start) >= 6:
                pad = 8
                lines.append(
                    (
                        max(by0, y_start - pad),
                        min(by1, y_end + pad),
                        max(0, min(xs) - 16),
                        min(width - 1, max(xs) + 16),
                    )
                )
        else:
            y += 1

    # Merge overlapping line boxes
    merged: list[tuple[int, int, int, int]] = []
    for box in lines:
        if not merged:
            merged.append(box)
            continue
        py0, py1, px0, px1 = merged[-1]
        y0b, y1b, x0b, x1b = box
        if y0b <= py1 + 4:
            merged[-1] = (py0, max(py1, y1b), min(px0, x0b), max(px1, x1b))
        else:
            merged.append(box)

    regions: list[TextRegion] = []
    for y0b, y1b, x0b, x1b in merged[-max_lines:]:
        # Sample fill from dark banner pixels inside the box
        samples: list[tuple[int, int, int]] = []
        for yy in range(y0b, y1b + 1, 2):
            for xx in range(x0b, x1b, 4):
                pix = im.getpixel((xx, yy))
                if _is_banner_pixel(*pix):
                    samples.append(pix)
        if samples:
            fill = tuple(sorted(samples)[len(samples) // 2])  # type: ignore[assignment]
            fill_rgb = (fill[0], fill[1], fill[2])
        else:
            fill_rgb = (10, 47, 66)

        box_h = max(18, y1b - y0b + 1)
        # Widen a bit so shorter replacements still look centered in the date slot
        cx = (x0b + x1b) // 2
        box_w = max(x1b - x0b + 1, int(width * 0.72))
        x = max(0, min(width - box_w, cx - box_w // 2))
        regions.append(
            TextRegion(
                x=x,
                y=y0b,
                w=box_w,
                h=box_h,
                fill_rgb=fill_rgb,
                fontsize=max(22, min(56, int(box_h * 0.72))),
            )
        )

    return regions


def pick_regions_for_replacements(
    frame_path: Path, count: int
) -> list[TextRegion]:
    """Map N replacements onto the bottom-most N banner text lines."""
    if count <= 0:
        return []
    lines = locate_lower_banner_text_lines(frame_path, max_lines=max(5, count + 2))
    # Keep thin teal banner glyph rows; drop huge false-positive blobs.
    good = [
        r
        for r in lines
        if 14 <= r.h <= 72 and _is_banner_pixel(*r.fill_rgb) and r.w >= 200
    ]
    if not good:
        good = [r for r in lines if 14 <= r.h <= 90]
    if not good:
        return []
    chosen = list(reversed(good))[:count]
    return list(reversed(chosen))

