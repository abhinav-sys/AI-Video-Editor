from __future__ import annotations

"""Heal old glyphs under OCR spans so redraw has no opaque stamp box."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.core.logging import get_logger
from app.services.text_locate import _is_banner_pixel, _luma, _median_rgb
from app.services.text_ocr import RenderRegion

logger = get_logger(__name__)

# Near-white overlay glyphs (not beige walls / skin / bokeh).
_GLYPH_MIN_CHANNEL = 175
_GLYPH_MAX_CHROMA = 95
_GLYPH_LUMA_FALLBACK = 210
_DILATE_ITERS = 3


@dataclass(frozen=True)
class HealedPatch:
    """RGBA patch that replaces healed pixels at (x, y) on the video."""

    x: int
    y: int
    path: Path
    w: int
    h: int
    mode: str  # "banner" | "inpaint"
    from_text: str
    text: str  # replacement to (for pairing with drawtext)
    t_start: float | None = None
    t_end: float | None = None


def build_glyph_mask(
    crop: Image.Image,
    *,
    min_channel: int = _GLYPH_MIN_CHANNEL,
    max_chroma: int = _GLYPH_MAX_CHROMA,
    luma_fallback: int = _GLYPH_LUMA_FALLBACK,
    dilate: int = _DILATE_ITERS,
) -> np.ndarray:
    """
    Binary mask (uint8 0/255) of near-white glyph pixels, dilated for AA edges.

    Uses min(R,G,B) + low chroma so beige/skin/purple bokeh are not treated as text
    (those caused solid muddy stamp plates on video backgrounds).
    """
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    r = rgb[:, :, 0].astype(np.int16)
    g = rgb[:, :, 1].astype(np.int16)
    b = rgb[:, :, 2].astype(np.int16)
    mn = np.minimum(np.minimum(r, g), b)
    mx = np.maximum(np.maximum(r, g), b)
    chroma = mx - mn
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b).astype(np.float32)
    near_white = (mn >= min_channel) & (chroma <= max_chroma)
    very_bright = (luma >= luma_fallback) & (chroma <= max_chroma + 20)
    mask = (near_white | very_bright).astype(np.uint8) * 255
    if dilate > 0:
        try:
            import cv2  # type: ignore

            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            mask = cv2.dilate(mask, kernel, iterations=dilate)
        except Exception:
            # Manual 1px expand fallback
            expanded = mask.copy()
            for _ in range(dilate):
                padded = np.pad(expanded, 1, mode="constant")
                neigh = np.maximum.reduce(
                    [
                        padded[0:-2, 0:-2],
                        padded[0:-2, 1:-1],
                        padded[0:-2, 2:],
                        padded[1:-1, 0:-2],
                        padded[1:-1, 2:],
                        padded[2:, 0:-2],
                        padded[2:, 1:-1],
                        padded[2:, 2:],
                        padded[1:-1, 1:-1],
                    ]
                )
                expanded = neigh
            mask = expanded
    return mask


def _feather_alpha(mask: np.ndarray, soft: int = 2) -> np.ndarray:
    """Soft edge alpha from binary mask (keeps core fully opaque)."""
    if soft <= 0:
        return mask
    try:
        import cv2  # type: ignore

        # Distance inside mask → fade near boundary
        binary = (mask >= 128).astype(np.uint8)
        dist = cv2.distanceTransform(binary, cv2.DIST_L2, 3)
        alpha = np.clip(dist / float(soft), 0.0, 1.0)
        return (alpha * 255.0).astype(np.uint8)
    except Exception:
        return mask


def region_is_banner_background(crop: Image.Image, mask: np.ndarray) -> bool:
    """True if most non-glyph pixels look like navy event-banner fill."""
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    if h * w == 0:
        return False
    bg = mask < 128
    if not np.any(bg):
        return True
    banner_count = 0
    total = 0
    # Subsample for speed — require real navy banner pixels, not merely dark video.
    ys, xs = np.where(bg)
    step = max(1, len(xs) // 400)
    for i in range(0, len(xs), step):
        r, g, b = (int(rgb[ys[i], xs[i], c]) for c in range(3))
        total += 1
        if _is_banner_pixel(r, g, b):
            banner_count += 1
    if total == 0:
        return False
    return (banner_count / total) >= 0.55


def region_is_flat_plate(crop: Image.Image, mask: np.ndarray) -> bool:
    """
    True if non-glyph pixels are a near-solid graphic plate (any hue).

    Headline city on this Godrej clip sits on a flat magenta panel — not live video.
    Treat that as soft-fill (median plate color), not OpenCV inpaint.
    Crops often include a seam into live video; use robust median distance so the
    seam does not force the inpaint path.
    """
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    bg = mask < 128
    if not np.any(bg):
        return True
    samples = rgb[bg].astype(np.float32)
    if samples.shape[0] < 30:
        return False
    step = max(1, samples.shape[0] // 1000)
    samples = samples[::step]
    med = np.median(samples, axis=0)
    # Dark foliage / night plates are not soft-fill candidates
    if float(0.2126 * med[0] + 0.7152 * med[1] + 0.0722 * med[2]) < 70:
        return False
    dist = np.linalg.norm(samples - med, axis=1)
    close = float((dist <= 30.0).mean())
    return close >= 0.62


def _plate_fill_color(crop: Image.Image, mask: np.ndarray) -> tuple[int, int, int]:
    """Median of solid-plate bg pixels; exclude glyphs, near-black, and bright speculars."""
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    samples: list[tuple[int, int, int]] = []
    ys, xs = np.where(mask < 128)
    step = max(1, len(xs) // 800) if len(xs) else 1
    for i in range(0, len(xs), step):
        y, x = int(ys[i]), int(xs[i])
        pix = (int(rgb[y, x, 0]), int(rgb[y, x, 1]), int(rgb[y, x, 2]))
        lum = _luma(*pix)
        # Keep saturated mid-plate colors (magenta panel); drop shadows / seams
        if 40 <= lum <= 230:
            samples.append(pix)
    if len(samples) < 15:
        for i in range(0, len(xs), step):
            y, x = int(ys[i]), int(xs[i])
            samples.append((int(rgb[y, x, 0]), int(rgb[y, x, 1]), int(rgb[y, x, 2])))
    return _median_rgb(samples) if samples else (180, 90, 170)


def _banner_fill_color(crop: Image.Image, mask: np.ndarray) -> tuple[int, int, int]:
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8)
    samples: list[tuple[int, int, int]] = []
    # Prefer strict navy banner pixels away from glyphs (avoids lighter smear patches)
    ys, xs = np.where(mask < 128)
    step = max(1, len(xs) // 500) if len(xs) else 1
    for i in range(0, len(xs), step):
        y, x = int(ys[i]), int(xs[i])
        pix = (int(rgb[y, x, 0]), int(rgb[y, x, 1]), int(rgb[y, x, 2]))
        if _is_banner_pixel(*pix):
            samples.append(pix)
    if len(samples) < 20:
        for i in range(0, len(xs), step):
            y, x = int(ys[i]), int(xs[i])
            pix = (int(rgb[y, x, 0]), int(rgb[y, x, 1]), int(rgb[y, x, 2]))
            if _luma(*pix) < 95:
                samples.append(pix)
    if not samples:
        ys, xs = np.where(mask >= 128)
        for i in range(0, min(len(xs), 200), max(1, len(xs) // 200 or 1)):
            y, x = int(ys[i]), int(xs[i])
            for dy in (-3, -2, -1, 1, 2, 3):
                for dx in (-3, -2, -1, 1, 2, 3):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < rgb.shape[0] and 0 <= nx < rgb.shape[1] and mask[ny, nx] < 128:
                        samples.append(
                            (int(rgb[ny, nx, 0]), int(rgb[ny, nx, 1]), int(rgb[ny, nx, 2]))
                        )
    return _median_rgb(samples) if samples else (10, 47, 66)


def heal_crop(
    crop: Image.Image,
    mask: np.ndarray,
    *,
    force_mode: str | None = None,
    prefer_fill: tuple[int, int, int] | None = None,
) -> tuple[Image.Image, str]:
    """
    Return healed RGB crop + mode ('banner' | 'flat' | 'inpaint').
    Banner: navy event strip — median navy fill.
    Flat: solid graphic plate (any hue) — median plate fill.
    Else: OpenCV inpaint so live video background shows through.
    """
    rgb = np.asarray(crop.convert("RGB"), dtype=np.uint8).copy()
    if force_mode == "banner" or (
        force_mode is None and region_is_banner_background(crop, mask)
    ):
        fill = prefer_fill if prefer_fill and _is_banner_pixel(*prefer_fill) else _banner_fill_color(crop, mask)
        mode = "banner"
    elif force_mode == "flat" or (force_mode is None and region_is_flat_plate(crop, mask)):
        # Always prefer measured plate median (style fill_rgb can be wrong ambient)
        fill = _plate_fill_color(crop, mask)
        mode = "flat"
    elif force_mode == "inpaint":
        fill = None
        mode = "inpaint"
    else:
        fill = None
        mode = "inpaint"

    if mode in ("banner", "flat") and fill is not None:
        ys, xs = np.where(mask >= 128)
        rgb[ys, xs] = np.array(fill, dtype=np.uint8)
        try:
            import cv2  # type: ignore

            edge = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
            edge = cv2.subtract(edge, mask)
            ey, ex = np.where(edge >= 128)
            for y, x in zip(ey.tolist(), ex.tolist()):
                orig = rgb[y, x].astype(np.float32)
                blended = 0.55 * np.array(fill, dtype=np.float32) + 0.45 * orig
                rgb[y, x] = blended.astype(np.uint8)
        except Exception:
            pass
        return Image.fromarray(rgb, mode="RGB"), mode

    # Inpaint path
    try:
        import cv2  # type: ignore

        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        healed = cv2.inpaint(bgr, mask, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        out = cv2.cvtColor(healed, cv2.COLOR_BGR2RGB)
        return Image.fromarray(out, mode="RGB"), "inpaint"
    except Exception as exc:
        logger.warning("OpenCV inpaint failed (%s); falling back to flat fill", exc)
        fill = prefer_fill or _plate_fill_color(crop, mask)
        ys, xs = np.where(mask >= 128)
        rgb[ys, xs] = np.array(fill, dtype=np.uint8)
        return Image.fromarray(rgb, mode="RGB"), "flat"


def heal_region_to_rgba_patch(
    frame: Image.Image,
    region: RenderRegion,
    *,
    pad: int = 4,
) -> tuple[Image.Image, int, int, str]:
    """
    Heal the region on `frame` and return (RGBA patch, x, y, mode).

    Alpha is the dilated glyph mask (feathered) so only healed glyphs overlay —
    never a solid plate of ambient fill.
    """
    fw, fh = frame.size
    x0 = max(0, region.x - pad)
    y0 = max(0, region.y - pad)
    x1 = min(fw, region.x + region.w + pad)
    y1 = min(fh, region.y + region.h + pad)
    if x1 <= x0 or y1 <= y0:
        empty = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        return empty, x0, y0, "banner"

    crop = frame.crop((x0, y0, x1, y1))
    mask = build_glyph_mask(crop)
    glyph_px = int((mask >= 128).sum())
    # Last resort: lower threshold once — never flood-fill the whole rectangle
    # (that recreates opaque stamp boxes on video backgrounds).
    if glyph_px < 12:
        mask = build_glyph_mask(
            crop,
            min_channel=150,
            max_chroma=120,
            luma_fallback=185,
            dilate=_DILATE_ITERS + 1,
        )
        glyph_px = int((mask >= 128).sum())
    if glyph_px < 8:
        logger.warning(
            "Sparse glyph mask (%d px) for %r — patch may leave leftovers",
            glyph_px,
            region.from_text,
        )

    healed_rgb, mode = heal_crop(crop, mask)
    # Capture plate/banner fill from the *tight* glyph mask before dilation expands
    # away all clean bg samples (otherwise fill collapses to dark seam colors).
    plate_fill = _plate_fill_color(crop, mask) if mode == "flat" else None
    banner_fill = _banner_fill_color(crop, mask) if mode == "banner" else None

    if mode in ("banner", "flat"):
        try:
            import cv2  # type: ignore

            mask = cv2.dilate(
                mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2
            )
            healed_rgb, mode = heal_crop(crop, mask, force_mode=mode)
        except Exception:
            pass

    # Flat solid plates: opaque plate-colored rect over glyph bbox (invisible on plate).
    if mode == "flat":
        ys, xs = np.where(mask >= 128)
        if len(xs) > 0:
            pad_b = 3
            by0 = max(0, int(ys.min()) - pad_b)
            by1 = min(mask.shape[0], int(ys.max()) + 1 + pad_b)
            bx0 = max(0, int(xs.min()) - pad_b)
            bx1 = min(mask.shape[1], int(xs.max()) + 1 + pad_b)
            fill = plate_fill or _plate_fill_color(crop, mask)
            patch = Image.new("RGBA", (bx1 - bx0, by1 - by0), (*fill, 255))
            return patch, x0 + bx0, y0 + by0, mode

    # Banner: paint dilated glyphs with pre-dilation navy fill
    if mode == "banner" and banner_fill is not None:
        rgb = np.asarray(healed_rgb.convert("RGB"), dtype=np.uint8).copy()
        ys, xs = np.where(mask >= 128)
        rgb[ys, xs] = np.array(banner_fill, dtype=np.uint8)
        healed_rgb = Image.fromarray(rgb, mode="RGB")

    rgba = healed_rgb.convert("RGBA")
    soft = 2 if mode == "inpaint" else 1
    alpha_arr = _feather_alpha(mask, soft=soft)
    rgba_np = np.asarray(rgba).copy()
    rgba_np[:, :, 3] = alpha_arr
    transparent = alpha_arr < 8
    if np.any(transparent):
        op = alpha_arr >= 128
        if np.any(op):
            under = np.median(rgba_np[:, :, :3][op], axis=0).astype(np.uint8)
        else:
            under = np.array([180, 90, 170], dtype=np.uint8)
        rgba_np[transparent, 0] = under[0]
        rgba_np[transparent, 1] = under[1]
        rgba_np[transparent, 2] = under[2]
    rgba = Image.fromarray(rgba_np, mode="RGBA")
    return rgba, x0, y0, mode


def write_healed_patches(
    frame_path: Path,
    regions: list[RenderRegion],
    out_dir: Path,
    *,
    pad: int = 4,
) -> list[HealedPatch]:
    """Write one RGBA PNG patch per region; return overlay descriptors."""
    patches, _mask = write_heal_artifacts(frame_path, regions, out_dir, pad=pad)
    return patches


def write_heal_artifacts(
    frame_path: Path,
    regions: list[RenderRegion],
    out_dir: Path,
    *,
    pad: int = 4,
) -> tuple[list[HealedPatch], Path | None]:
    """
    Write banner RGBA overlays + optional full-frame removelogo mask for video-bg glyphs.

    Banner regions → static soft-fill overlays (navy is constant across frames).
    Inpaint regions → removelogo mask (white=remove) so FFmpeg heals *each frame*
    instead of freezing one still's background as a muddy stamp plate.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = Image.open(frame_path).convert("RGB")
    fw, fh = frame.size
    patches: list[HealedPatch] = []
    removelogo = np.zeros((fh, fw), dtype=np.uint8)
    has_inpaint = False
    try:
        for i, region in enumerate(regions):
            rgba, x, y, mode = heal_region_to_rgba_patch(frame, region, pad=pad)
            dest = out_dir / f"heal_{i}_{region.from_text[:12].replace(' ', '_')}.png"
            rgba.save(dest)
            patches.append(
                HealedPatch(
                    x=x,
                    y=y,
                    path=dest,
                    w=rgba.size[0],
                    h=rgba.size[1],
                    mode=mode,
                    from_text=region.from_text,
                    text=region.text,
                    t_start=getattr(region, "t_start", None),
                    t_end=getattr(region, "t_end", None),
                )
            )
            # Flat plates + live video both need per-frame heal: static fill from one
            # heal_source stamps the wrong color onto other scenes.
            if mode in ("inpaint", "flat"):
                has_inpaint = True
                # Rebuild a binary glyph mask on the crop for removelogo (not opaque flat rect)
                crop = frame.crop((x, y, min(fw, x + rgba.size[0]), min(fh, y + rgba.size[1])))
                gmask = build_glyph_mask(crop)
                try:
                    import cv2  # type: ignore
                    gmask = cv2.dilate(
                        gmask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2
                    )
                except Exception:
                    pass
                y1 = min(fh, y + gmask.shape[0])
                x1 = min(fw, x + gmask.shape[1])
                ah, aw = y1 - y, x1 - x
                if ah > 0 and aw > 0:
                    removelogo[y:y1, x:x1] = np.maximum(
                        removelogo[y:y1, x:x1],
                        (gmask[:ah, :aw] >= 128).astype(np.uint8) * 255,
                    )
                # Force patch mode so FFmpeg skips static overlay for this region
                patches[-1] = HealedPatch(
                    x=patches[-1].x,
                    y=patches[-1].y,
                    path=patches[-1].path,
                    w=patches[-1].w,
                    h=patches[-1].h,
                    mode="inpaint",
                    from_text=patches[-1].from_text,
                    text=patches[-1].text,
                    t_start=patches[-1].t_start,
                    t_end=patches[-1].t_end,
                )
            logger.info(
                "Healed patch %d mode=%s %dx%d at (%d,%d) for %r -> %r",
                i,
                mode,
                rgba.size[0],
                rgba.size[1],
                x,
                y,
                region.from_text,
                region.text,
            )
        mask_path: Path | None = None
        if has_inpaint and int(removelogo.sum()) > 0:
            mask_path = out_dir / "removelogo_mask.png"
            Image.fromarray(removelogo, mode="L").save(mask_path)
            logger.info("Wrote removelogo mask %s (%d white px)", mask_path, int((removelogo >= 128).sum()))
    finally:
        frame.close()
    return patches, mask_path


def inpaint_video_under_mask(
    input_path: Path,
    mask_path: Path,
    output_path: Path,
    *,
    inpaint_radius: int = 3,
) -> Path:
    """
    Per-frame OpenCV inpaint under a fixed glyph mask (burned-in overlays).

    Only the mask bounding box is processed each frame — much cheaper than full-frame
    removelogo, and avoids removelogo's soft rectangular plate.
    """
    import cv2  # type: ignore

    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise RuntimeError(f"Cannot read removelogo mask: {mask_path}")
    ys, xs = np.where(mask >= 128)
    if len(xs) == 0:
        # Nothing to heal — copy through via ffmpeg-less path: just fail soft
        raise RuntimeError("Inpaint mask is empty")
    pad = 6
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(mask.shape[0], int(ys.max()) + 1 + pad)
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(mask.shape[1], int(xs.max()) + 1 + pad)
    mcrop = mask[y0:y1, x0:x1]

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video for inpaint: {input_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if mask.shape[0] != h or mask.shape[1] != w:
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        ys, xs = np.where(mask >= 128)
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(mask.shape[0], int(ys.max()) + 1 + pad)
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(mask.shape[1], int(xs.max()) + 1 + pad)
        mcrop = mask[y0:y1, x0:x1]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # MJPEG/AVI is a reliable OpenCV intermediate; FFmpeg will re-encode later.
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (w, h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"Cannot open VideoWriter: {output_path}")

    frames = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            crop = frame[y0:y1, x0:x1]
            healed = cv2.inpaint(crop, mcrop, inpaint_radius, cv2.INPAINT_TELEA)
            frame[y0:y1, x0:x1] = healed
            writer.write(frame)
            frames += 1
    finally:
        cap.release()
        writer.release()
    logger.info("OpenCV inpaint wrote %d frames -> %s", frames, output_path)
    if frames == 0:
        raise RuntimeError("OpenCV inpaint produced zero frames")
    return output_path


def paint_invisible_preview(
    frame_path: Path,
    regions: list[RenderRegion],
    dest: Path,
    *,
    patches: list[HealedPatch] | None = None,
) -> Path:
    """Compose healed patches + drawtext-like redraw for audit preview."""
    from PIL import ImageDraw, ImageFont

    im = Image.open(frame_path).convert("RGBA")
    if patches is None:
        tmp = dest.parent / "_heal_preview_patches"
        patches = write_healed_patches(frame_path, regions, tmp)

    for patch in patches:
        overlay = Image.open(patch.path).convert("RGBA")
        im.alpha_composite(overlay, dest=(patch.x, patch.y))
        overlay.close()

    draw = ImageDraw.Draw(im)
    for region in regions:
        try:
            font = ImageFont.truetype(
                region.fontfile or "arial.ttf", max(10, region.fontsize)
            )
        except Exception:
            try:
                font = ImageFont.truetype(
                    "arialbd.ttf" if region.bold else "arial.ttf", max(10, region.fontsize)
                )
            except Exception:
                font = ImageFont.load_default()
        text_y = region.text_y if region.text_y else region.y + max(0, (region.h - region.fontsize) // 2)
        if region.align == "center":
            bbox = draw.textbbox((0, 0), region.text, font=font)
            tw = bbox[2] - bbox[0]
            text_x = region.x + max(0, (region.w - tw) // 2)
        else:
            text_x = region.x + 2
        draw.text((text_x, text_y), region.text, fill=(*region.font_rgb, 255), font=font)

    dest.parent.mkdir(parents=True, exist_ok=True)
    out = im.convert("RGB")
    out.save(dest)
    im.close()
    out.close()
    return dest
