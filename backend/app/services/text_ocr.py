from __future__ import annotations

"""Full-frame OCR + fuzzy match for text-only replacements."""

import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageEnhance, ImageOps

from app.api.schemas.edits import TextReplace
from app.core.logging import get_logger
from app.services.text_locate import StyleSample, sample_style_from_box

logger = get_logger(__name__)

_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9&]+")
_AMP_RE = re.compile(r"\s*&\s*|\s+and\s+", re.IGNORECASE)
_FILLER_WORDS = frozenset({"of", "the", "on", "in", "at", "to", "a", "an", "for", "and", "&"})
# EasyOCR often reads leading 1 as I/l in ordinals: "I5th" → "15th"
_OCR_ORDINAL_I_RE = re.compile(r"\b[Il](\d*)(st|nd|rd|th)\b", re.IGNORECASE)
_OCR_LEADING_I_DIGIT_RE = re.compile(r"(?<![a-z0-9])[Il](?=\d)", re.IGNORECASE)

# Boxes that overlap at least this much (same from_) are treated as one occurrence.
_IOU_DEDUP = 0.35
# Center distance (fraction of frame diagonal) for near-duplicate regions.
_CENTER_DEDUP_FRAC = 0.04
_MIN_CONF = 0.25
_MIN_CONF_SHORT = 0.18
_UPSCALE = 1.75


@dataclass(frozen=True)
class OcrBox:
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float


@dataclass(frozen=True)
class RenderRegion:
    """One healed span + drawtext slot with sampled style.

    `text` is what FFmpeg paints (usually only the replacement `to`).
    Old glyphs are removed via healed RGBA patches (no opaque drawbox).
    Optional t_start/t_end gate the drawtext via enable=between.
    """

    x: int
    y: int
    w: int
    h: int
    fill_rgb: tuple[int, int, int]
    font_rgb: tuple[int, int, int]
    fontsize: int
    text: str
    align: str
    from_text: str
    ocr_text: str
    bold: bool = False
    baseline_y: int = 0
    fontfile: str | None = None
    text_y: int = 0
    t_start: float | None = None
    t_end: float | None = None
    entity_id: str | None = None


def normalize_text(value: str) -> str:
    """Lowercase, drop ordinals, collapse separators for fuzzy contains checks."""
    text = value.lower().strip()
    # Fix common OCR confusions before ordinal stripping
    text = _OCR_ORDINAL_I_RE.sub(r"1\1\2", text)
    text = _OCR_LEADING_I_DIGIT_RE.sub("1", text)
    text = _ORDINAL_RE.sub(r"\1", text)
    text = _AMP_RE.sub(" & ", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _significant_tokens(value: str) -> list[str]:
    return [t for t in normalize_text(value).split() if t and t not in _FILLER_WORDS]


def texts_match(haystack: str, needle: str) -> bool:
    """True if needle appears in haystack after normalization (substring / ordered tokens)."""
    n = normalize_text(needle)
    h = normalize_text(haystack)
    if not n or not h:
        return False
    if n in h:
        return True

    n_tokens = _significant_tokens(needle)
    h_tokens = _significant_tokens(haystack)
    if not n_tokens:
        return False
    if len(n_tokens) == 1:
        return n_tokens[0] in h_tokens

    for i in range(len(h_tokens) - len(n_tokens) + 1):
        if h_tokens[i : i + len(n_tokens)] == n_tokens:
            return True

    # Digits + month can be split with OCR noise between them (e.g. "15 1 16 august")
    # Require all significant tokens present in order (not necessarily contiguous).
    if len(n_tokens) >= 2:
        pos = 0
        for tok in n_tokens:
            found = False
            for j in range(pos, len(h_tokens)):
                if h_tokens[j] == tok:
                    pos = j + 1
                    found = True
                    break
            if not found:
                return False
        return True
    return False


def stitch_line_boxes(boxes: list[OcrBox], *, y_tol_frac: float = 0.55, gap_frac: float = 1.8) -> list[OcrBox]:
    """Merge left-to-right OCR fragments on the same text line into longer boxes.

    WhatsApp / compressed overlays often split \"15TH & 16TH OF AUGUST\" into
    several adjacent boxes that individually fail fuzzy date matching.
    """
    if len(boxes) < 2:
        return list(boxes)

    ordered = sorted(boxes, key=lambda b: (b.y + b.h / 2, b.x))
    used = [False] * len(ordered)
    stitched: list[OcrBox] = []

    for i, seed in enumerate(ordered):
        if used[i]:
            continue
        group = [seed]
        used[i] = True
        changed = True
        while changed:
            changed = False
            group.sort(key=lambda b: b.x)
            g_y = sum(b.y + b.h / 2 for b in group) / len(group)
            g_h = max(b.h for b in group)
            g_right = max(b.x + b.w for b in group)
            g_left = min(b.x for b in group)
            for j, cand in enumerate(ordered):
                if used[j]:
                    continue
                cy = cand.y + cand.h / 2
                if abs(cy - g_y) > max(8.0, g_h * y_tol_frac):
                    continue
                gap = max(0, cand.x - g_right)
                left_gap = max(0, g_left - (cand.x + cand.w))
                max_gap = max(12.0, g_h * gap_frac)
                if gap <= max_gap or left_gap <= max_gap:
                    group.append(cand)
                    used[j] = True
                    changed = True
                    g_right = max(g_right, cand.x + cand.w)
                    g_left = min(g_left, cand.x)

        if len(group) == 1:
            stitched.append(group[0])
            continue
        group.sort(key=lambda b: b.x)
        text = " ".join(b.text for b in group)
        x0 = min(b.x for b in group)
        y0 = min(b.y for b in group)
        x1 = max(b.x + b.w for b in group)
        y1 = max(b.y + b.h for b in group)
        conf = sum(b.confidence for b in group) / len(group)
        stitched.append(
            OcrBox(
                text=text,
                x=x0,
                y=y0,
                w=max(1, x1 - x0),
                h=max(1, y1 - y0),
                confidence=conf,
            )
        )

    # Prefer longer stitched lines over the fragments they cover
    out = list(boxes)
    for box in stitched:
        overlapping = [e for e in out if box_iou(box, e) >= 0.2]
        if not overlapping:
            if box not in out:
                out.append(box)
            continue
        max_orig_len = max(len(e.text) for e in overlapping)
        if len(box.text) > max_orig_len + 1:
            out = [e for e in out if box_iou(box, e) < 0.2]
            out.append(box)
    return out


def _suffix_after(ocr_text: str, end: int) -> str:
    rest = ocr_text[end:]
    if rest.startswith(","):
        return rest
    if rest and not rest[0].isspace() and not rest.startswith("|"):
        return " " + rest.lstrip(" ,")
    return rest


def _token_spans(ocr_text: str) -> list[tuple[str, int, int]]:
    """Raw whitespace tokens with character offsets."""
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", ocr_text)]


def find_match_char_range(ocr_text: str, from_: str) -> tuple[int, int] | None:
    """
    Character [start, end) of the matched `from_` phrase inside ocr_text.
    Uses exact then fuzzy token alignment (ordinals / fillers ignored).
    """
    if not ocr_text or not from_:
        return None

    pattern = re.compile(re.escape(from_), re.IGNORECASE)
    m = pattern.search(ocr_text)
    if m:
        return m.start(), m.end()

    n_from = _significant_tokens(from_)
    if not n_from:
        return None

    tokens = _token_spans(ocr_text)
    indexed: list[tuple[int, str]] = []
    for i, (raw, _start, _end) in enumerate(tokens):
        nt = normalize_text(raw)
        if not nt or nt in _FILLER_WORDS:
            continue
        indexed.append((i, nt))

    flat = [nt for _, nt in indexed]
    for i in range(len(flat) - len(n_from) + 1):
        if flat[i : i + len(n_from)] == n_from:
            start_idx = indexed[i][0]
            end_idx = indexed[i + len(n_from) - 1][0]
            return tokens[start_idx][1], tokens[end_idx][2]

    if len(n_from) == 1:
        for raw, start, end in tokens:
            if normalize_text(raw) == n_from[0]:
                return start, end

    if normalize_text(ocr_text) == normalize_text(from_):
        return 0, len(ocr_text)
    return None


def estimate_glyph_span(
    box: OcrBox,
    from_: str,
    to: str,
    *,
    fontsize: int,
    frame_w: int,
    pad: int = 4,
) -> tuple[int, int, int, int, str]:
    """
    Return (x, y, w, h, draw_text) covering only the matched glyphs.

    draw_text is always `to` (surgical). Extends rightward slightly if `to`
    is longer — never widens to a banner-wide band.
    """
    y = max(0, box.y - pad)
    h = box.h + 2 * pad

    char_range = find_match_char_range(box.text, from_)
    text_len = max(1, len(box.text))

    # Whole-box match: OCR line is essentially just the from_ phrase
    whole = (
        char_range is None
        or (char_range[0] == 0 and char_range[1] >= text_len - 1)
        or normalize_text(box.text) == normalize_text(from_)
        or (
            char_range is not None
            and not box.text[: char_range[0]].strip()
            and not box.text[char_range[1] :].strip()
        )
    )

    if whole or char_range is None:
        x = max(0, box.x - pad)
        w = box.w + 2 * pad
    else:
        start, end = char_range
        # Proportional glyph span inside the OCR box
        start_frac = start / text_len
        end_frac = end / text_len
        # Bias left: proportional char widths under-cover bold first glyphs (S→SMelbourne)
        start_frac = max(0.0, start_frac - 0.04)
        end_frac = min(1.0, end_frac + 0.03)
        span_x = box.x + int(box.w * start_frac)
        span_w = max(8, int(box.w * (end_frac - start_frac)))
        left_extra = pad + (6 if len(from_.strip()) <= 12 else 0)
        x = max(0, span_x - left_extra)
        w = span_w + pad + left_extra

    # If replacement is longer, extend right only (stay near the OCR box).
    needed = max(w, int(len(to) * max(8, fontsize) * 0.55))
    if needed > w:
        # Cap: OCR box right edge + 20% of box width slack, never > 55% frame
        max_right = min(
            frame_w,
            box.x + box.w + max(24, int(box.w * 0.2)),
            x + int(frame_w * 0.55),
        )
        w = min(needed, max(1, max_right - x))

    w = max(1, min(w, frame_w - x))
    h = max(1, h)
    return x, y, w, h, to


def apply_substring_replace(ocr_text: str, from_: str, to: str) -> str:
    """
    Rebuild the on-screen line: swap the matched phrase, keep the rest
    (year, time, surrounding words). Used for logic/tests; surgical paint
    draws only `to` over the matched span.
    """
    char_range = find_match_char_range(ocr_text, from_)
    if char_range is None:
        return to if normalize_text(ocr_text) == normalize_text(from_) else ocr_text

    start, end = char_range
    prefix = ocr_text[:start]
    if prefix.strip():
        return prefix.rstrip(" ,") + " " + to + _suffix_after(ocr_text, end)
    return to + _suffix_after(ocr_text, end)


def box_iou(a: OcrBox | RenderRegion, b: OcrBox | RenderRegion) -> float:
    ax2, ay2 = a.x + a.w, a.y + a.h
    bx2, by2 = b.x + b.w, b.y + b.h
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def _center_distance(a: OcrBox | RenderRegion, b: OcrBox | RenderRegion) -> float:
    ax, ay = a.x + a.w / 2, a.y + a.h / 2
    bx, by = b.x + b.w / 2, b.y + b.h / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def is_short_token_from(from_text: str) -> bool:
    """Single significant token (typical city / badge) — often appears in multiple places."""
    return len(_significant_tokens(from_text)) == 1


def is_portrait_frame(frame_w: int, frame_h: int) -> bool:
    return frame_h > 0 and (frame_w / frame_h) < 0.75


@lru_cache(maxsize=1)
def _get_easyocr_reader():
    try:
        import easyocr  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "EasyOCR is required for text replacement. "
            "Install backend requirements (easyocr) and retry."
        ) from exc
    # English only; CPU is fine for sample frames
    return easyocr.Reader(["en"], gpu=False, verbose=False)


def _parse_easyocr_results(
    results: list,
    *,
    scale: float = 1.0,
    min_conf: float = _MIN_CONF,
) -> list[OcrBox]:
    boxes: list[OcrBox] = []
    for item in results:
        if not item or len(item) < 3:
            continue
        bbox, text, conf = item[0], str(item[1]).strip(), float(item[2])
        if not text or conf < min_conf:
            continue
        xs = [int(p[0] / scale) for p in bbox]
        ys = [int(p[1] / scale) for p in bbox]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        boxes.append(
            OcrBox(
                text=text,
                x=x0,
                y=y0,
                w=max(1, x1 - x0),
                h=max(1, y1 - y0),
                confidence=conf,
            )
        )
    return boxes


def _merge_box_lists(*lists: list[OcrBox]) -> list[OcrBox]:
    """Union OCR boxes, keeping higher-confidence / larger when overlapping."""
    merged: list[OcrBox] = []
    for boxes in lists:
        for box in boxes:
            dup_idx: int | None = None
            for i, existing in enumerate(merged):
                if box_iou(box, existing) >= _IOU_DEDUP:
                    dup_idx = i
                    break
            if dup_idx is None:
                merged.append(box)
                continue
            existing = merged[dup_idx]
            prefer_new = (
                box.confidence > existing.confidence
                or (box.confidence == existing.confidence and box.w * box.h > existing.w * existing.h)
            )
            if prefer_new:
                merged[dup_idx] = box
    return merged


def _prepare_boosted_image(frame_path: Path, dest: Path, scale: float = _UPSCALE) -> float:
    """Write contrast-boosted upscaled copy; return scale used."""
    im = Image.open(frame_path).convert("RGB")
    w, h = im.size
    boosted = ImageOps.autocontrast(im, cutoff=1)
    boosted = ImageEnhance.Contrast(boosted).enhance(1.35)
    boosted = ImageEnhance.Sharpness(boosted).enhance(1.2)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    boosted = boosted.resize((nw, nh), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    boosted.save(dest)
    im.close()
    return scale


def _easyocr_readtext(image_path: Path, *, reader_factory: Callable[[], object] | None) -> list:
    """Run EasyOCR readtext in-process or via subprocess (default)."""
    if reader_factory is not None:
        reader = reader_factory()
        return reader.readtext(str(image_path))  # type: ignore[attr-defined]

    from app.config import get_settings

    if get_settings().ocr_subprocess:
        from app.services.easyocr_subprocess import get_easyocr_subprocess_client

        return get_easyocr_subprocess_client().readtext(image_path)

    reader = _get_easyocr_reader()
    return reader.readtext(str(image_path))  # type: ignore[attr-defined]


def read_ocr_boxes(
    frame_path: Path,
    *,
    reader_factory: Callable[[], object] | None = None,
    boosted: bool = False,
    min_conf: float | None = None,
) -> list[OcrBox]:
    """Run OCR over the full frame and return bounding boxes."""
    path = Path(frame_path)
    if not path.is_file():
        return []

    conf = min_conf if min_conf is not None else _MIN_CONF

    if not boosted:
        results = _easyocr_readtext(path, reader_factory=reader_factory)
        return _parse_easyocr_results(results, scale=1.0, min_conf=conf)

    with tempfile.TemporaryDirectory(prefix="vgai_ocr_boost_") as tmp:
        boosted_path = Path(tmp) / "boosted.png"
        scale = _prepare_boosted_image(path, boosted_path)
        results = _easyocr_readtext(boosted_path, reader_factory=reader_factory)
        return _parse_easyocr_results(results, scale=scale, min_conf=conf)


def read_ocr_boxes_robust(
    frame_path: Path,
    *,
    reader_factory: Callable[[], object] | None = None,
) -> list[OcrBox]:
    """Normal OCR plus upscaled/contrast pass, merged and deduped."""
    normal = read_ocr_boxes(frame_path, reader_factory=reader_factory, boosted=False)
    try:
        boosted = read_ocr_boxes(
            frame_path,
            reader_factory=reader_factory,
            boosted=True,
            min_conf=_MIN_CONF_SHORT,
        )
    except Exception as exc:
        logger.warning("Boosted OCR failed on %s: %s", frame_path, exc)
        boosted = []
    return _merge_box_lists(normal, boosted)


def prefer_tight_match_boxes(from_text: str, hits: list[OcrBox]) -> list[OcrBox]:
    """
    For short tokens (cities), drop long banner/headline boxes when a tighter
    box already covers the same screen location — keep distinct occurrences.
    """
    if not hits or not is_short_token_from(from_text):
        return hits

    n_from = normalize_text(from_text)

    # Sort tightest first (exact / short OCR text, then smaller area)
    def tightness(b: OcrBox) -> tuple[int, int, int]:
        exact = 0 if normalize_text(b.text) == n_from else 1
        return (exact, len(b.text), b.w * b.h)

    ordered = sorted(hits, key=tightness)
    kept: list[OcrBox] = []
    for box in ordered:
        near = [
            k
            for k in kept
            if box_iou(box, k) >= 0.15 or _center_distance(box, k) < max(48.0, k.h * 2.5)
        ]
        if near:
            # Same occurrence already covered by a tighter box
            continue
        # Refuse absurd whole-headline blobs as a standalone hit when a
        # reasonable alternative exists elsewhere in the list.
        text_n = normalize_text(box.text)
        absurd = len(text_n) > max(28, len(n_from) * 8) or box.h > 100
        if absurd:
            # Only keep if nothing else was kept and no tighter candidates remain
            has_tighter = any(
                tightness(o) < tightness(box) for o in ordered if o is not box
            )
            if has_tighter or kept:
                continue
        kept.append(box)

    return kept or hits[:1]


def match_replacements_to_boxes(
    boxes: list[OcrBox],
    replacements: list[TextReplace],
    *,
    match_all: bool = True,
) -> dict[str, list[OcrBox]]:
    """
    Map each replacement.from_ to matching OCR boxes.
    Keys are from_ strings. Missing keys mean no match.
    """
    search_boxes = stitch_line_boxes(boxes)
    found: dict[str, list[OcrBox]] = {pair.from_: [] for pair in replacements}
    for pair in replacements:
        hits = [b for b in search_boxes if texts_match(b.text, pair.from_)]
        hits = prefer_tight_match_boxes(pair.from_, hits)
        if match_all:
            found[pair.from_] = hits
        else:
            found[pair.from_] = hits[:1]
    return found


def build_render_regions(
    frame_path: Path,
    replacements: list[TextReplace],
    boxes: list[OcrBox],
    *,
    match_all: bool = True,
    pad: int = 4,
    require_all_from: bool = True,
) -> list[RenderRegion]:
    """
    For each matching OCR box, cover only the matched glyph span and paint `to`.

    Raises ValueError if any from_ has zero matches (when require_all_from).
    """
    if not replacements:
        return []

    matched = match_replacements_to_boxes(boxes, replacements, match_all=match_all)
    if require_all_from:
        missing = [pair.from_ for pair in replacements if not matched.get(pair.from_)]
        if missing:
            seen = ", ".join(repr(b.text) for b in boxes[:12]) or "(none)"
            raise ValueError(
                "Text not found on frame for: "
                + ", ".join(repr(m) for m in missing)
                + f". OCR saw: {seen}. "
                "No silent overwrite — check spelling or try another clip."
            )

    im = Image.open(frame_path)
    frame_w, frame_h = im.size
    im.close()

    to_by_from = {pair.from_: pair.to for pair in replacements}
    regions: list[RenderRegion] = []

    for from_text, hits in matched.items():
        if not hits:
            continue
        to_text = to_by_from[from_text]
        for box in hits:
            # Initial size estimate from full OCR box
            style: StyleSample = sample_style_from_box(
                frame_path, box.x, box.y, box.w, box.h, frame_width=frame_w
            )
            x, y, w, h, draw_text = estimate_glyph_span(
                box,
                from_text,
                to_text,
                fontsize=style.fontsize,
                frame_w=frame_w,
                pad=pad + (2 if is_short_token_from(from_text) else 0),
            )
            # Cap height to OCR box + pad — never grow into banner chrome
            h = min(h, box.h + 2 * pad)
            h = min(frame_h - y, max(1, h))
            w = min(frame_w - x, max(1, w))
            # Short tokens: ensure cover is at least as wide as replacement estimate
            if is_short_token_from(from_text):
                min_w = max(w, int(len(to_text) * max(8, min(style.fontsize, h)) * 0.52))
                w = min(frame_w - x, min_w)

            # Matched phrase text for font scoring (prefer exact from_ when whole box)
            char_range = find_match_char_range(box.text, from_text)
            if char_range is not None:
                probe = box.text[char_range[0] : char_range[1]]
            else:
                probe = from_text

            # Precise sample on the surgical span + closest system font
            style = sample_style_from_box(
                frame_path,
                x,
                y,
                w,
                h,
                frame_width=frame_w,
                sample_text=probe,
                match_font=True,
            )
            # Never let style fontsize explode past the cover box
            fontsize = min(style.fontsize, max(12, int(h * 0.92)))
            style = StyleSample(
                fill_rgb=style.fill_rgb,
                font_rgb=style.font_rgb,
                fontsize=fontsize,
                align=style.align,
                baseline_y=style.baseline_y,
                fontfile=style.fontfile,
                bold=style.bold,
            )

            # Left-align when span is not near frame center (keeps pin / neighbors)
            cx = x + w / 2
            if abs(cx - frame_w / 2) < frame_w * 0.12 and w >= box.w * 0.85:
                align = "center"
            elif abs(cx - frame_w / 2) < frame_w * 0.12 and normalize_text(box.text) == normalize_text(
                from_text
            ):
                align = "center"
            else:
                align = "left"

            baseline = style.baseline_y if style.baseline_y else (y + h - 2)
            # FFmpeg drawtext y is top of glyphs; sit on measured baseline
            text_y = int(baseline - style.fontsize * 0.85)
            text_y = max(y, min(y + h - max(8, style.fontsize // 2), text_y))

            regions.append(
                RenderRegion(
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    fill_rgb=style.fill_rgb,
                    font_rgb=style.font_rgb,
                    fontsize=style.fontsize,
                    text=draw_text,
                    align=align,
                    from_text=from_text,
                    ocr_text=box.text,
                    bold=style.bold,
                    baseline_y=baseline,
                    fontfile=style.fontfile,
                    text_y=text_y,
                )
            )

    # Stable paint order: top→bottom so lower banner draws last
    regions.sort(key=lambda r: (r.y, r.x))
    return regions


def merge_render_regions(
    regions: list[RenderRegion],
    *,
    frame_w: int,
    frame_h: int,
) -> list[RenderRegion]:
    """
    Deduplicate regions across frames: same from_text + overlapping IoU
    or near-identical centers → keep larger / more specific ocr_text.
    """
    if not regions:
        return []

    diag = max(1.0, (frame_w**2 + frame_h**2) ** 0.5)
    center_limit = diag * _CENTER_DEDUP_FRAC
    merged: list[RenderRegion] = []

    for region in regions:
        dup_idx: int | None = None
        for i, existing in enumerate(merged):
            if existing.from_text != region.from_text:
                continue
            if box_iou(region, existing) >= _IOU_DEDUP:
                dup_idx = i
                break
            if _center_distance(region, existing) <= center_limit:
                dup_idx = i
                break
        if dup_idx is None:
            merged.append(region)
            continue

        existing = merged[dup_idx]
        # Prefer more specific OCR line; if equally specific, prefer larger paint box
        short = is_short_token_from(region.from_text)
        if short and abs(len(region.ocr_text) - len(existing.ocr_text)) >= 4:
            prefer_new = len(region.ocr_text) < len(existing.ocr_text)
        else:
            prefer_new = (
                region.w * region.h > existing.w * existing.h
                or (
                    region.w * region.h == existing.w * existing.h
                    and len(region.ocr_text) > len(existing.ocr_text)
                )
            )
        if prefer_new:
            merged[dup_idx] = region

    merged.sort(key=lambda r: (r.y, r.x))
    return merged


def count_regions_by_from(regions: list[RenderRegion]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in regions:
        counts[r.from_text] = counts.get(r.from_text, 0) + 1
    return counts


def assert_no_suspicious_partial(
    regions: list[RenderRegion],
    replacements: list[TextReplace],
    *,
    frame_w: int,
    frame_h: int,
) -> None:
    """
    On portrait (9:16-ish) frames, a short city-like from_ with only one hit
    usually means OCR missed the hero overlay — fail instead of mixed cities.
    """
    if not is_portrait_frame(frame_w, frame_h):
        return

    counts = count_regions_by_from(regions)
    for pair in replacements:
        if not is_short_token_from(pair.from_):
            continue
        n = counts.get(pair.from_, 0)
        if n == 1:
            raise ValueError(
                f"Found {pair.from_!r} in 1 place; expected multiple overlays on this "
                f"portrait clip — check OCR or try another sample. "
                f"Refusing partial replace that would leave mixed text on screen."
            )


def ocr_best_regions_for_replacements(
    frame_paths: list[Path],
    replacements: list[TextReplace],
    *,
    reader_factory: Callable[[], object] | None = None,
    use_boost: bool = False,
    enforce_multi_short: bool = True,
) -> tuple[list[RenderRegion], Path | None]:
    """
    OCR all sample frames, union matching regions (IoU dedupe), optionally
    retry with boosted OCR when short tokens look incomplete.

    Default use_boost=False (single EasyOCR pass) for Windows stability;
    boost retries only when portrait short-token coverage looks partial.
    """
    if not replacements:
        return [], None
    if not frame_paths:
        raise ValueError("No sample frames for OCR")

    def _collect(boosted_pass: bool) -> tuple[list[RenderRegion], Path | None, int, int, Exception | None]:
        all_regions: list[RenderRegion] = []
        best_frame: Path | None = None
        best_score = -1
        frame_w = frame_h = 0
        last_error: Exception | None = None
        any_success = False

        for frame_path in frame_paths:
            try:
                if boosted_pass or use_boost:
                    boxes = read_ocr_boxes_robust(frame_path, reader_factory=reader_factory)
                else:
                    boxes = read_ocr_boxes(frame_path, reader_factory=reader_factory)
                # Per-frame: require all from_ present when possible; if a frame
                # is missing some, still take partial regions for union.
                try:
                    regions = build_render_regions(
                        frame_path, replacements, boxes, require_all_from=True
                    )
                except ValueError as missing_exc:
                    last_error = missing_exc
                    regions = build_render_regions(
                        frame_path, replacements, boxes, require_all_from=False
                    )
                    if not regions:
                        logger.warning("OCR match failed on %s: %s", frame_path, missing_exc)
                        continue

                im = Image.open(frame_path)
                fw, fh = im.size
                im.close()
                frame_w, frame_h = fw, fh
                any_success = True
                score = len(regions)
                if score > best_score:
                    best_score = score
                    best_frame = frame_path
                all_regions.extend(regions)
            except Exception as exc:
                last_error = exc
                logger.warning("OCR match failed on %s: %s", frame_path, exc)
                continue

        if not any_success or not all_regions:
            return [], None, 0, 0, last_error

        merged = merge_render_regions(all_regions, frame_w=frame_w, frame_h=frame_h)

        # Ensure every from_ appears at least once after union
        present = {r.from_text for r in merged}
        missing = [p.from_ for p in replacements if p.from_ not in present]
        if missing:
            sample_texts: list[str] = []
            try:
                # Best-effort: show what the best frame contained
                if best_frame is not None:
                    sample_boxes = read_ocr_boxes_robust(best_frame, reader_factory=reader_factory)
                    sample_texts = [b.text for b in sample_boxes[:12]]
            except Exception:
                sample_texts = []
            seen = ", ".join(repr(t) for t in sample_texts) or "(unavailable)"
            err = ValueError(
                "Text not found across sample frames for: "
                + ", ".join(repr(m) for m in missing)
                + f". OCR saw: {seen}. "
                "No silent overwrite — check spelling or try another clip."
            )
            return merged, best_frame, frame_w, frame_h, err

        return merged, best_frame, frame_w, frame_h, None

    # First pass (robust OCR when use_boost)
    regions, best_frame, fw, fh, err = _collect(boosted_pass=False)

    if err is not None and not regions:
        raise err
    if not regions:
        if err:
            raise err
        raise ValueError("OCR found no matching text regions for the requested replacements")

    # If any from_ still missing entirely, fail
    present = {r.from_text for r in regions}
    missing = [p.from_ for p in replacements if p.from_ not in present]
    if missing:
        raise ValueError(
            "Text not found across sample frames for: "
            + ", ".join(repr(m) for m in missing)
            + ". No silent overwrite — check spelling or try another clip."
        )

    if enforce_multi_short and fw and fh:
        try:
            assert_no_suspicious_partial(regions, replacements, frame_w=fw, frame_h=fh)
        except ValueError as partial_exc:
            logger.warning("Suspicious partial short-token match; retrying boosted OCR: %s", partial_exc)
            # Force another boosted union pass (already robust; still retry once)
            regions2, best2, fw2, fh2, err2 = _collect(boosted_pass=True)
            if regions2:
                regions, best_frame = regions2, best2 or best_frame
                fw, fh = fw2 or fw, fh2 or fh
            if err2 and not regions2:
                raise err2 from partial_exc
            try:
                assert_no_suspicious_partial(regions, replacements, frame_w=fw, frame_h=fh)
            except ValueError as partial_exc2:
                # Boost already ran; failing the whole job leaves mixed cities worse
                # than replacing the one clear hit we found.
                logger.warning(
                    "Portrait short-token still looks partial after boost; "
                    "proceeding with %d region(s): %s",
                    len(regions),
                    partial_exc2,
                )

    return regions, best_frame
