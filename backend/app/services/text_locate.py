from __future__ import annotations

"""Locate burned-in text lines and sample style from known boxes."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class TextRegion:
    x: int
    y: int
    w: int
    h: int
    fill_rgb: tuple[int, int, int]
    fontsize: int


@dataclass(frozen=True)
class StyleSample:
    fill_rgb: tuple[int, int, int]
    font_rgb: tuple[int, int, int]
    fontsize: int
    align: str  # "center" | "left"
    baseline_y: int = 0
    fontfile: str | None = None
    bold: bool = False


def _luma(r: int, g: int, b: int) -> float:
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(r: int, g: int, b: int) -> float:
    mx, mn = max(r, g, b), min(r, g, b)
    if mx <= 0:
        return 0.0
    return (mx - mn) / mx


def _is_banner_pixel(r: int, g: int, b: int) -> bool:
    """Dark teal / navy fill typical of event banners."""
    lum = _luma(r, g, b)
    return lum < 90 and b >= r and g >= r - 10 and (g + b) > 60


def _median_rgb(samples: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not samples:
        return (10, 47, 66)
    rs = sorted(s[0] for s in samples)
    gs = sorted(s[1] for s in samples)
    bs = sorted(s[2] for s in samples)
    mid = len(samples) // 2
    return (rs[mid], gs[mid], bs[mid])


def _top_percentile_rgb(
    samples: list[tuple[int, int, int]],
    *,
    fraction: float = 0.2,
) -> tuple[int, int, int]:
    """
    Median of the brightest / most saturated glyph pixels so warm gold
    hero text is not washed to plain white.
    """
    if not samples:
        return (255, 255, 255)
    scored = sorted(
        samples,
        key=lambda p: (_luma(*p) + 40.0 * _saturation(*p)),
        reverse=True,
    )
    n = max(1, int(len(scored) * fraction))
    return _median_rgb(scored[:n])


@lru_cache(maxsize=1)
def list_font_candidates() -> tuple[tuple[str, bool], ...]:
    """(path, is_bold) pairs that exist on this machine. Bundled fonts first."""
    from app.config import get_settings

    named: list[tuple[str, bool]] = []
    fonts_dir = get_settings().resolved_fonts_dir
    if fonts_dir.is_dir():
        named.extend(
            [
                (str(fonts_dir / "DejaVuSans.ttf"), False),
                (str(fonts_dir / "DejaVuSans-Bold.ttf"), True),
                (str(fonts_dir / "LiberationSans-Regular.ttf"), False),
                (str(fonts_dir / "LiberationSans-Bold.ttf"), True),
                (str(fonts_dir / "Arial.ttf"), False),
                (str(fonts_dir / "arial.ttf"), False),
                (str(fonts_dir / "arialbd.ttf"), True),
            ]
        )
    named.extend(
        [
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", True),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", False),
            ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", True),
            ("/System/Library/Fonts/Supplemental/Arial.ttf", False),
            ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", True),
            ("/Library/Fonts/Arial.ttf", False),
            ("/Library/Fonts/Arial Bold.ttf", True),
        ]
    )
    windir = os.environ.get("WINDIR")
    win_fonts = Path(windir) / "Fonts" if windir else Path(r"C:\Windows\Fonts")
    named.extend(
        [
            (str(win_fonts / "arial.ttf"), False),
            (str(win_fonts / "arialbd.ttf"), True),
            (str(win_fonts / "segoeui.ttf"), False),
            (str(win_fonts / "segoeuib.ttf"), True),
            (str(win_fonts / "calibri.ttf"), False),
            (str(win_fonts / "calibrib.ttf"), True),
        ]
    )
    seen: set[str] = set()
    out: list[tuple[str, bool]] = []
    for path, bold in named:
        if path in seen:
            continue
        if Path(path).is_file():
            seen.add(path)
            out.append((path, bold))
    return tuple(out)


def match_font_to_crop(
    frame_path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
    sample_text: str,
    *,
    fontsize: int,
    font_rgb: tuple[int, int, int],
) -> tuple[str | None, bool]:
    """
    Render `sample_text` with candidate fonts; pick lowest MSE vs bright crop.
    Returns (fontfile, bold).
    """
    candidates = list_font_candidates()
    if not candidates or not sample_text.strip():
        fallback = candidates[0] if candidates else (None, False)
        return fallback[0] if fallback else None, bool(fallback[1]) if fallback else False

    im = Image.open(frame_path).convert("RGB")
    width, height = im.size
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + max(1, w))
    y1 = min(height, y + max(1, h))
    if x1 <= x0 or y1 <= y0:
        im.close()
        candidates = list_font_candidates()
        if not candidates:
            return None, False
        return candidates[0][0], candidates[0][1]
    crop = im.crop((x0, y0, x1, y1))
    im.close()

    cw, ch = crop.size
    if cw < 4 or ch < 4:
        return candidates[0][0], candidates[0][1]

    # Bright mask on original (glyph pixels)
    crop_px = crop.load()
    bright_coords: list[tuple[int, int]] = []
    for yy in range(ch):
        for xx in range(cw):
            if _luma(*crop_px[xx, yy]) > 130:
                bright_coords.append((xx, yy))
    if len(bright_coords) < 8:
        # Fall back to all pixels
        bright_coords = [(xx, yy) for yy in range(0, ch, 2) for xx in range(0, cw, 2)]

    best_path: str | None = None
    best_bold = False
    best_score = float("inf")
    probe = sample_text.strip()[:48] or "A"

    for font_path, is_bold in candidates:
        try:
            font = ImageFont.truetype(font_path, max(10, fontsize))
        except OSError:
            continue
        canvas = Image.new("RGB", (cw, ch), (0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        # Approximate left/baseline placement similar to drawtext
        try:
            bbox = draw.textbbox((0, 0), probe, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = cw // 2, ch // 2
        tx = max(0, (cw - tw) // 2)
        ty = max(0, (ch - th) // 2)
        draw.text((tx, ty), probe, font=font, fill=font_rgb)

        rend = canvas.load()
        err = 0.0
        n = 0
        for xx, yy in bright_coords:
            o = crop_px[xx, yy]
            r = rend[xx, yy]
            # Prefer matching where original was bright
            err += (o[0] - r[0]) ** 2 + (o[1] - r[1]) ** 2 + (o[2] - r[2]) ** 2
            n += 1
        if n == 0:
            continue
        score = err / n
        # Slight preference for bold on tall hero boxes
        if ch >= 48 and is_bold:
            score *= 0.97
        if score < best_score:
            best_score = score
            best_path = font_path
            best_bold = is_bold

    if best_path is None:
        return candidates[0][0], candidates[0][1]
    return best_path, best_bold


def sample_style_from_box(
    frame_path: Path,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    frame_width: int | None = None,
    sample_text: str | None = None,
    match_font: bool = False,
) -> StyleSample:
    """
    Sample fill + glyph color + fontsize + baseline from a text bounding box.

    Uses top-percentile bright pixels for color and measured glyph height for size.
    """
    im = Image.open(frame_path).convert("RGB")
    width, height = im.size
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + max(1, w))
    y1 = min(height, y + max(1, h))
    if x1 <= x0 or y1 <= y0:
        fallback = list_font_candidates()
        fontfile = fallback[0][0] if fallback else None
        bold = fallback[0][1] if fallback else False
        im.close()
        return StyleSample(
            fill_rgb=(10, 47, 66),
            font_rgb=(255, 255, 255),
            fontsize=max(14, min(120, max(1, h) or 24)),
            align="left",
            baseline_y=max(0, min(height - 1, y + max(1, h))),
            fontfile=fontfile,
            bold=bold,
        )

    fills: list[tuple[int, int, int]] = []
    glyphs: list[tuple[int, int, int, int, int]] = []  # r,g,b,xx,yy
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            pix = im.getpixel((xx, yy))
            lum = _luma(*pix)
            if _is_banner_pixel(*pix) or lum < 110:
                fills.append(pix)
            if lum > 145:
                glyphs.append((pix[0], pix[1], pix[2], xx, yy))

    # Tighter fill: prefer dark pixels near bright glyphs (2px ring)
    if glyphs:
        ring: list[tuple[int, int, int]] = []
        glyph_set = {(gx, gy) for _r, _g, _b, gx, gy in glyphs}
        for _r, _g, _b, gx, gy in glyphs:
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = gx + dx, gy + dy
                    if (nx, ny) in glyph_set:
                        continue
                    if x0 <= nx < x1 and y0 <= ny < y1:
                        pix = im.getpixel((nx, ny))
                        if _luma(*pix) < 120:
                            ring.append(pix)
        if ring:
            fills = ring

    fill_rgb = _median_rgb(fills) if fills else (10, 47, 66)
    glyph_rgbs = [(r, g, b) for r, g, b, _x, _y in glyphs]
    font_rgb = _top_percentile_rgb(glyph_rgbs) if glyph_rgbs else (255, 255, 255)

    if glyphs:
        ys = [gy for _r, _g, _b, _x, gy in glyphs]
        glyph_top, glyph_bot = min(ys), max(ys)
        glyph_h = max(8, glyph_bot - glyph_top + 1)
        baseline_y = glyph_bot
        fontsize = max(14, min(120, int(glyph_h / 0.78)))
    else:
        box_h = max(18, y1 - y0)
        fontsize = max(14, min(120, int(box_h * 0.72)))
        baseline_y = y1 - 2

    fw = frame_width or width
    cx = (x0 + x1) / 2
    align = "center" if abs(cx - fw / 2) < fw * 0.12 else "left"

    fontfile: str | None = None
    bold = fontsize >= 40 or (y1 - y0) >= 48
    if match_font and sample_text:
        fontfile, bold = match_font_to_crop(
            frame_path,
            x0,
            y0,
            x1 - x0,
            y1 - y0,
            sample_text,
            fontsize=fontsize,
            font_rgb=font_rgb,
        )

    im.close()
    return StyleSample(
        fill_rgb=fill_rgb,
        font_rgb=font_rgb,
        fontsize=fontsize,
        align=align,
        baseline_y=baseline_y,
        fontfile=fontfile,
        bold=bold,
    )


def locate_lower_banner_text_lines(frame_path: Path, max_lines: int = 3) -> list[TextRegion]:
    """
    Find bright-on-dark text rows inside the dominant lower-third banner.

    Kept as a fill helper / legacy fallback for banner-shaped layouts.
    """
    im = Image.open(frame_path).convert("RGB")
    width, height = im.size
    y0 = int(height * 0.52)
    y1 = int(height * 0.92)

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

    threshold = max(40, int((width * 0.95 - width * 0.05) / 2 * 0.35))
    banner_ys = [y for y in range(y0, y1) if banner_score[y] >= threshold]
    if len(banner_ys) < 20:
        return []

    by0, by1 = min(banner_ys), max(banner_ys)

    lines: list[tuple[int, int, int, int]] = []
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
        samples: list[tuple[int, int, int]] = []
        for yy in range(y0b, y1b + 1, 2):
            for xx in range(x0b, x1b, 4):
                pix = im.getpixel((xx, yy))
                if _is_banner_pixel(*pix):
                    samples.append(pix)
        fill_rgb = _median_rgb(samples) if samples else (10, 47, 66)

        box_h = max(18, y1b - y0b + 1)
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


def pick_regions_for_replacements(frame_path: Path, count: int) -> list[TextRegion]:
    """Legacy: map N replacements onto the bottom-most N banner text lines."""
    if count <= 0:
        return []
    lines = locate_lower_banner_text_lines(frame_path, max_lines=max(5, count + 2))
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
