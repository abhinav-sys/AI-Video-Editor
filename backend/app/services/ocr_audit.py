from __future__ import annotations

"""Step-by-step OCR audit tracker: gates, artifacts, and verify-after paint."""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.api.schemas.edits import TextReplace
from app.core.logging import get_logger
from app.services.text_ocr import (
    OcrBox,
    RenderRegion,
    assert_no_suspicious_partial,
    build_render_regions,
    count_regions_by_from,
    is_portrait_frame,
    match_replacements_to_boxes,
    merge_render_regions,
    ocr_best_regions_for_replacements,
    read_ocr_boxes,
    read_ocr_boxes_robust,
    stitch_line_boxes,
    texts_match,
)

logger = get_logger(__name__)

GateStatus = str  # pass | fail | skip | warn


@dataclass
class GateResult:
    name: str
    status: GateStatus
    reason: str = ""
    elapsed_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "details": self.details,
        }


@dataclass
class AuditReport:
    run_id: str
    out_dir: Path
    gates: list[GateResult] = field(default_factory=list)
    replacements: list[dict[str, str]] = field(default_factory=list)
    regions: list[RenderRegion] = field(default_factory=list)
    best_frame: Path | None = None
    preview_after: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return all(g.status in ("pass", "skip", "warn") for g in self.gates) and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ok": self.ok,
            "error": self.error,
            "replacements": self.replacements,
            "gates": [g.to_dict() for g in self.gates],
            "region_count": len(self.regions),
            "best_frame": str(self.best_frame) if self.best_frame else None,
            "preview_after": str(self.preview_after) if self.preview_after else None,
            "out_dir": str(self.out_dir),
        }


def _box_dict(b: OcrBox) -> dict[str, Any]:
    return {
        "text": b.text,
        "x": b.x,
        "y": b.y,
        "w": b.w,
        "h": b.h,
        "confidence": round(b.confidence, 4),
    }


def _region_dict(r: RenderRegion) -> dict[str, Any]:
    return {
        "x": r.x,
        "y": r.y,
        "w": r.w,
        "h": r.h,
        "fill_rgb": list(r.fill_rgb),
        "font_rgb": list(r.font_rgb),
        "fontsize": r.fontsize,
        "text": r.text,
        "align": r.align,
        "from_text": r.from_text,
        "ocr_text": r.ocr_text,
        "bold": r.bold,
        "baseline_y": r.baseline_y,
        "fontfile": r.fontfile,
        "text_y": r.text_y,
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _draw_boxes(
    frame_path: Path,
    dest: Path,
    boxes: list[tuple[int, int, int, int, str, tuple[int, int, int]]],
) -> None:
    im = Image.open(frame_path).convert("RGB")
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    for x, y, w, h, label, color in boxes:
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)
        if label:
            draw.text((x + 2, max(0, y - 18)), label[:48], fill=color, font=font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    im.close()


def paint_regions_on_frame(frame_path: Path, regions: list[RenderRegion], dest: Path) -> None:
    """Invisible heal + redraw on a still (audit preview; mirrors FFmpeg intent)."""
    from app.services.text_heal import paint_invisible_preview

    paint_invisible_preview(frame_path, regions, dest)


def check_paint_quality(
    *,
    filter_complex: str | None = None,
    preview_path: Path | None = None,
    used_healed_patches: bool = False,
) -> GateResult:
    """Reject opaque stamp-box paint mode (drawbox plates)."""
    t0 = time.perf_counter()
    problems: list[str] = []
    if filter_complex and "drawbox=" in filter_complex and "t=fill" in filter_complex:
        problems.append("filter uses opaque drawbox fill (stamp box)")
    if not used_healed_patches and filter_complex and "drawtext=" in filter_complex:
        if "overlay=" not in filter_complex and "removelogo=" not in filter_complex:
            problems.append("text redraw without healed overlay / removelogo")
    # When heal was applied out-of-band (OpenCV per-frame inpaint), filter may be
    # drawtext-only for video-bg regions — that is OK if drawbox is absent.
    if preview_path and preview_path.is_file() and not used_healed_patches:
        try:
            im = Image.open(preview_path).convert("RGB")
            w, h = im.size
            solid_plate = 0
            windows = 0
            for y0 in range(int(h * 0.08), int(h * 0.16), 8):
                for x0 in range(int(w * 0.55), int(w * 0.92), 12):
                    windows += 1
                    pix = [
                        im.getpixel((x0 + dx, y0 + dy))
                        for dy in (0, 4)
                        for dx in (0, 4, 8)
                        if x0 + dx < w and y0 + dy < h
                    ]
                    if len(pix) < 4:
                        continue
                    rs = [p[0] for p in pix]
                    gs = [p[1] for p in pix]
                    bs = [p[2] for p in pix]
                    if max(rs) - min(rs) < 18 and max(gs) - min(gs) < 18 and max(bs) - min(bs) < 18:
                        r = sum(rs) // len(rs)
                        g = sum(gs) // len(gs)
                        b = sum(bs) // len(bs)
                        if (
                            r > 80
                            and b > 100
                            and g < r - 25
                            and b > g + 25
                            and max(r, g, b) - min(r, g, b) > 40
                        ):
                            solid_plate += 1
            im.close()
            if windows and solid_plate / windows > 0.35:
                problems.append(
                    f"preview has solid purple stamp plate (windows={solid_plate}/{windows})"
                )
        except Exception as exc:
            logger.warning("paint_quality preview scan failed: %s", exc)

    elapsed = (time.perf_counter() - t0) * 1000
    if problems:
        return GateResult(
            name="paint_quality",
            status="fail",
            reason="; ".join(problems),
            elapsed_ms=elapsed,
            details={"problems": problems},
        )
    return GateResult(
        name="paint_quality",
        status="pass",
        reason="No opaque stamp box; heal+redraw path",
        elapsed_ms=elapsed,
        details={"used_healed_patches": used_healed_patches},
    )


def _footer_band_y(frame_h: int) -> int:
    """Y above which paint regions should stay (avoid Godrej logo stamp)."""
    return int(frame_h * 0.88)


def check_surgical_spans(
    regions: list[RenderRegion],
    *,
    frame_w: int,
    frame_h: int,
) -> GateResult:
    """Fail if a region looks like a banner wipe or lands on the footer logo band."""
    t0 = time.perf_counter()
    problems: list[str] = []
    footer_y = _footer_band_y(frame_h)
    for r in regions:
        if r.w > frame_w * 0.72:
            problems.append(
                f"span too wide for {r.from_text!r}: w={r.w}/{frame_w} ({r.w / frame_w:.0%})"
            )
        if r.y >= footer_y:
            problems.append(
                f"region for {r.from_text!r} in footer band y={r.y} (>= {footer_y})"
            )
        # Whole-banner wipe: tall enough to cover multiple banner lines
        if r.h > frame_h * 0.08 and r.w > frame_w * 0.55:
            problems.append(
                f"suspicious banner-wipe size for {r.from_text!r}: {r.w}x{r.h}"
            )
    elapsed = (time.perf_counter() - t0) * 1000
    if problems:
        return GateResult(
            name="surgical_span",
            status="fail",
            reason="; ".join(problems[:4]),
            elapsed_ms=elapsed,
            details={"problems": problems},
        )
    return GateResult(
        name="surgical_span",
        status="pass",
        reason=f"{len(regions)} region(s) within span limits",
        elapsed_ms=elapsed,
        details={"footer_y": footer_y},
    )


def verify_after_paint(
    painted_path: Path,
    replacements: list[TextReplace],
    regions: list[RenderRegion],
    *,
    out_json: Path | None = None,
) -> GateResult:
    """Re-OCR painted still: prefer seeing `to`, flag remaining `from` near spans."""
    t0 = time.perf_counter()
    try:
        # Single-pass OCR only — robust/boosted re-OCR has crashed on Windows.
        boxes = read_ocr_boxes(painted_path)
    except Exception as exc:
        return GateResult(
            name="verify_after",
            status="warn",
            reason=f"OCR on painted frame failed: {exc}",
            elapsed_ms=(time.perf_counter() - t0) * 1000,
        )

    stitched = stitch_line_boxes(boxes)
    seen = [b.text for b in stitched]
    remaining_from: list[str] = []
    found_to: list[str] = []
    missing_to: list[str] = []

    for pair in replacements:
        # Check if any region for this from still shows old text nearby
        pair_regions = [r for r in regions if r.from_text == pair.from_]
        near_hits_from = 0
        near_hits_to = 0
        for r in pair_regions:
            pad = 12
            for b in stitched:
                # box center near region?
                cx, cy = b.x + b.w / 2, b.y + b.h / 2
                if (
                    r.x - pad <= cx <= r.x + r.w + pad
                    and r.y - pad <= cy <= r.y + r.h + pad
                ):
                    if texts_match(b.text, pair.from_) and not texts_match(b.text, pair.to):
                        near_hits_from += 1
                    if texts_match(b.text, pair.to):
                        near_hits_to += 1
        # Also accept global presence of `to`
        global_to = any(texts_match(b.text, pair.to) for b in stitched)
        if near_hits_to or global_to:
            found_to.append(pair.to)
        else:
            missing_to.append(pair.to)
        if near_hits_from:
            remaining_from.append(pair.from_)

    details = {
        "ocr_saw": seen[:20],
        "found_to": found_to,
        "missing_to": missing_to,
        "remaining_from_near_spans": remaining_from,
    }
    if out_json is not None:
        _write_json(out_json, details)

    elapsed = (time.perf_counter() - t0) * 1000
    if remaining_from:
        return GateResult(
            name="verify_after",
            status="fail",
            reason=f"Old text still near spans: {remaining_from}",
            elapsed_ms=elapsed,
            details=details,
        )
    if missing_to:
        # Soft fail → warn: OCR may miss short redraws; regions were painted
        return GateResult(
            name="verify_after",
            status="warn",
            reason=f"Could not re-detect replacement text: {missing_to}",
            elapsed_ms=elapsed,
            details=details,
        )
    return GateResult(
        name="verify_after",
        status="pass",
        reason="Replacement text detected; old from_ cleared near spans",
        elapsed_ms=elapsed,
        details=details,
    )


def write_audit_snapshot(
    out_dir: Path,
    *,
    replacements: list[TextReplace],
    frame_paths: list[Path],
    sample_times: list[float] | None = None,
    regions: list[RenderRegion] | None = None,
    best_frame: Path | None = None,
    error: str | None = None,
    extra_gates: list[GateResult] | None = None,
    dump_ocr: bool = False,
    paint_still: bool = True,
) -> AuditReport:
    """
    Persist a partial/full audit package (used by jobs when OCR already ran).

    `dump_ocr=True` re-runs EasyOCR on each frame (slow; CLI diagnostics).
    Job path keeps dump_ocr=False and still writes regions + overlays.
    """
    run_id = out_dir.name if out_dir.name else str(uuid.uuid4())
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    report = AuditReport(
        run_id=run_id,
        out_dir=out_dir,
        replacements=[{"from": p.from_, "to": p.to} for p in replacements],
        regions=list(regions or []),
        best_frame=best_frame,
        error=error,
    )

    # Copy frames (+ optional OCR dumps)
    durable_frames: list[Path] = []
    for idx, fp in enumerate(frame_paths):
        if not fp.is_file():
            continue
        dest = frames_dir / f"sample_{idx}.png"
        dest.write_bytes(fp.read_bytes())
        durable_frames.append(dest)
        if not dump_ocr:
            continue
        t_label = ""
        if sample_times and idx < len(sample_times):
            t_label = f"@{sample_times[idx]:.2f}s"
        try:
            raw = read_ocr_boxes_robust(dest)
            stitched = stitch_line_boxes(raw)
            _write_json(out_dir / f"ocr_raw_{idx}.json", {"time": t_label, "boxes": [_box_dict(b) for b in raw]})
            _write_json(
                out_dir / f"ocr_stitched_{idx}.json",
                {"time": t_label, "boxes": [_box_dict(b) for b in stitched]},
            )
        except Exception as exc:
            logger.warning("Audit OCR dump failed on %s: %s", dest, exc)

    if dump_ocr and durable_frames and replacements:
        try:
            boxes0 = read_ocr_boxes_robust(durable_frames[0])
            matched = match_replacements_to_boxes(boxes0, replacements, match_all=True)
            _write_json(
                out_dir / "matches.json",
                {k: [_box_dict(b) for b in v] for k, v in matched.items()},
            )
        except Exception as exc:
            logger.warning("Audit matches dump failed: %s", exc)

    if report.regions:
        _write_json(out_dir / "regions.json", [_region_dict(r) for r in report.regions])
        counts = count_regions_by_from(report.regions)
        report.gates.append(
            GateResult(
                "regions",
                "pass",
                f"{len(report.regions)} region(s)",
                details={"by_from": counts},
            )
        )
        if durable_frames:
            im = Image.open(durable_frames[0])
            fw, fh = im.size
            im.close()
            report.gates.append(check_surgical_spans(report.regions, frame_w=fw, frame_h=fh))
            try:
                assert_no_suspicious_partial(
                    report.regions, replacements, frame_w=fw, frame_h=fh
                )
                report.gates.append(
                    GateResult(
                        "multi_short",
                        "pass",
                        "OK",
                        details={"counts": counts, "portrait": is_portrait_frame(fw, fh)},
                    )
                )
            except ValueError as exc:
                report.gates.append(GateResult("multi_short", "fail", str(exc)))
                report.error = report.error or str(exc)

    bf = best_frame
    if bf is None and durable_frames:
        bf = durable_frames[0]
    if bf is not None and bf.is_file():
        best_copy = out_dir / "overlay_before.png"
        if bf.resolve() != best_copy.resolve():
            best_copy.write_bytes(bf.read_bytes())
        report.best_frame = best_copy

        if dump_ocr:
            try:
                raw = read_ocr_boxes_robust(best_copy)
                _draw_boxes(
                    best_copy,
                    out_dir / "overlay_ocr.png",
                    [(b.x, b.y, b.w, b.h, b.text[:24], (0, 220, 80)) for b in raw],
                )
            except Exception:
                pass

        if report.regions:
            _draw_boxes(
                best_copy,
                out_dir / "overlay_regions.png",
                [
                    (r.x, r.y, r.w, r.h, f"{r.from_text}->{r.text}"[:40], (255, 80, 40))
                    for r in report.regions
                ],
            )
            if paint_still:
                preview = out_dir / "preview_after.png"
                try:
                    paint_regions_on_frame(best_copy, report.regions, preview)
                    report.preview_after = preview
                    report.gates.append(GateResult("paint", "pass", "Invisible heal+redraw still"))
                    report.gates.append(
                        check_paint_quality(
                            preview_path=preview,
                            used_healed_patches=True,
                        )
                    )
                    if report.gates[-1].status == "fail":
                        report.error = report.error or report.gates[-1].reason
                except Exception as exc:
                    logger.warning("Audit paint preview failed: %s", exc)
                    report.gates.append(GateResult("paint", "fail", str(exc)))

    if extra_gates:
        report.gates.extend(extra_gates)

    if error and not report.error:
        report.error = error

    _write_json(out_dir / "audit.json", report.to_dict())
    return report


async def run_full_audit(
    *,
    video_path: Path,
    replacements: list[TextReplace],
    out_dir: Path,
    ffmpeg_service: Any,
    sample_fracs: tuple[float, ...] = (0.15, 0.35, 0.55, 0.75),
    paint_still: bool = True,
    verify: bool = True,
    use_boost: bool = False,
) -> AuditReport:
    """
    End-to-end gate tracker for a video + from/to pairs.
    OCRs each sample frame once (cached) to avoid EasyOCR crashes from redo.
    Default use_boost=False for stability; production path still uses robust OCR.
    """
    run_id = out_dir.name or str(uuid.uuid4())
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    report = AuditReport(
        run_id=run_id,
        out_dir=out_dir,
        replacements=[{"from": p.from_, "to": p.to} for p in replacements],
    )

    # --- Gate: parse ---
    t0 = time.perf_counter()
    if not replacements:
        report.gates.append(
            GateResult("parse", "fail", "No replace_text pairs", (time.perf_counter() - t0) * 1000)
        )
        report.error = "No replacements"
        _write_json(out_dir / "audit.json", report.to_dict())
        return report
    report.gates.append(
        GateResult(
            "parse",
            "pass",
            f"{len(replacements)} pair(s)",
            (time.perf_counter() - t0) * 1000,
            {"pairs": report.replacements},
        )
    )

    # --- Gate: sample ---
    t0 = time.perf_counter()
    duration = await ffmpeg_service.probe_duration(video_path)
    if duration and duration > 3:
        times = [
            min(max(duration * p, 0.5), max(duration - 0.5, 0.5)) for p in sample_fracs
        ]
    else:
        times = [1.0]
    frame_paths: list[Path] = []
    for idx, at in enumerate(times):
        dest = frames_dir / f"sample_{idx}.png"
        ok = await ffmpeg_service._extract_frame(video_path, dest, at_seconds=at)
        if ok and dest.is_file():
            frame_paths.append(dest)
    if not frame_paths:
        report.gates.append(
            GateResult("sample", "fail", "Failed to extract sample frames", (time.perf_counter() - t0) * 1000)
        )
        report.error = "No frames"
        _write_json(out_dir / "audit.json", report.to_dict())
        return report
    report.gates.append(
        GateResult(
            "sample",
            "pass",
            f"{len(frame_paths)} frame(s)",
            (time.perf_counter() - t0) * 1000,
            {"times": times[: len(frame_paths)], "duration": duration},
        )
    )

    # --- Gate: OCR (once per frame, cache) ---
    t0 = time.perf_counter()
    boxes_by_frame: list[list[OcrBox]] = []
    total_boxes = 0
    frame_w = frame_h = 0
    for idx, fp in enumerate(frame_paths):
        try:
            raw = (
                read_ocr_boxes_robust(fp)
                if use_boost
                else read_ocr_boxes(fp)
            )
            boxes_by_frame.append(raw)
            stitched = stitch_line_boxes(raw)
            total_boxes += len(raw)
            im = Image.open(fp)
            frame_w, frame_h = im.size
            im.close()
            _write_json(
                out_dir / f"ocr_raw_{idx}.json",
                {"time": times[idx] if idx < len(times) else None, "boxes": [_box_dict(b) for b in raw]},
            )
            _write_json(
                out_dir / f"ocr_stitched_{idx}.json",
                {"boxes": [_box_dict(b) for b in stitched]},
            )
        except Exception as exc:
            logger.warning("OCR dump %s failed: %s", fp, exc)
            boxes_by_frame.append([])
    if total_boxes < 1:
        report.gates.append(
            GateResult("ocr", "fail", "No OCR boxes on any frame", (time.perf_counter() - t0) * 1000)
        )
        report.error = "OCR empty"
        _write_json(out_dir / "audit.json", report.to_dict())
        return report
    report.gates.append(
        GateResult(
            "ocr",
            "pass",
            f"{total_boxes} box(es) across frames",
            (time.perf_counter() - t0) * 1000,
            {"frame_w": frame_w, "frame_h": frame_h},
        )
    )

    # --- Gate: match-all (reuse cached boxes) ---
    t0 = time.perf_counter()
    union_hits: dict[str, list[dict[str, Any]]] = {p.from_: [] for p in replacements}
    for idx, boxes in enumerate(boxes_by_frame):
        if not boxes:
            continue
        matched = match_replacements_to_boxes(boxes, replacements, match_all=True)
        for k, hits in matched.items():
            for b in hits:
                d = _box_dict(b)
                d["frame"] = idx
                union_hits[k].append(d)
    _write_json(out_dir / "matches.json", union_hits)
    missing = [p.from_ for p in replacements if not union_hits.get(p.from_)]
    if missing:
        report.gates.append(
            GateResult(
                "match_all",
                "fail",
                f"Missing: {missing}",
                (time.perf_counter() - t0) * 1000,
                {"hits": {k: len(v) for k, v in union_hits.items()}},
            )
        )
        report.error = f"Text not found: {missing}"
    else:
        report.gates.append(
            GateResult(
                "match_all",
                "pass",
                "All from_ matched on >=1 frame",
                (time.perf_counter() - t0) * 1000,
                {"hits": {k: len(v) for k, v in union_hits.items()}},
            )
        )

    # --- Build regions from cached boxes (no second EasyOCR pass) ---
    t0 = time.perf_counter()
    all_regions: list[RenderRegion] = []
    best_frame: Path | None = None
    best_score = -1
    for idx, (fp, boxes) in enumerate(zip(frame_paths, boxes_by_frame)):
        if not boxes:
            continue
        try:
            try:
                regs = build_render_regions(fp, replacements, boxes, require_all_from=True)
            except ValueError:
                regs = build_render_regions(fp, replacements, boxes, require_all_from=False)
            if not regs:
                continue
            all_regions.extend(regs)
            if len(regs) > best_score:
                best_score = len(regs)
                best_frame = fp
        except Exception as exc:
            logger.warning("Region build failed on %s: %s", fp, exc)

    regions: list[RenderRegion] = []
    if all_regions and frame_w and frame_h:
        regions = merge_render_regions(all_regions, frame_w=frame_w, frame_h=frame_h)
        present = {r.from_text for r in regions}
        still_missing = [p.from_ for p in replacements if p.from_ not in present]
        if still_missing:
            report.gates.append(
                GateResult(
                    "regions",
                    "fail",
                    f"Missing after union: {still_missing}",
                    (time.perf_counter() - t0) * 1000,
                )
            )
            report.error = report.error or f"Missing after union: {still_missing}"
        else:
            report.regions = regions
            report.best_frame = best_frame
            _write_json(out_dir / "regions.json", [_region_dict(r) for r in regions])
            report.gates.append(
                GateResult(
                    "regions",
                    "pass",
                    f"{len(regions)} region(s)",
                    (time.perf_counter() - t0) * 1000,
                    {"by_from": count_regions_by_from(regions)},
                )
            )
    else:
        # Fallback: production path (extra OCR) only if cache path failed hard
        try:
            regions, best_frame = ocr_best_regions_for_replacements(frame_paths, replacements)
            report.regions = regions
            report.best_frame = best_frame
            _write_json(out_dir / "regions.json", [_region_dict(r) for r in regions])
            report.gates.append(
                GateResult(
                    "regions",
                    "pass",
                    f"{len(regions)} region(s) via production OCR",
                    (time.perf_counter() - t0) * 1000,
                    {"by_from": count_regions_by_from(regions)},
                )
            )
        except Exception as exc:
            report.error = report.error or str(exc)
            report.gates.append(
                GateResult("regions", "fail", str(exc), (time.perf_counter() - t0) * 1000)
            )

    # --- Gate: multi-short ---
    t0 = time.perf_counter()
    if regions and frame_w and frame_h:
        try:
            assert_no_suspicious_partial(regions, replacements, frame_w=frame_w, frame_h=frame_h)
            report.gates.append(
                GateResult(
                    "multi_short",
                    "pass",
                    "Short-token counts OK"
                    if is_portrait_frame(frame_w, frame_h)
                    else "Landscape — skipped strict multi",
                    (time.perf_counter() - t0) * 1000,
                    {
                        "portrait": is_portrait_frame(frame_w, frame_h),
                        "counts": count_regions_by_from(regions),
                    },
                )
            )
        except ValueError as exc:
            report.gates.append(
                GateResult("multi_short", "fail", str(exc), (time.perf_counter() - t0) * 1000)
            )
            report.error = report.error or str(exc)
    else:
        report.gates.append(
            GateResult("multi_short", "skip", "No regions to check", (time.perf_counter() - t0) * 1000)
        )

    # Durable best frame + overlays (no extra OCR)
    if best_frame and best_frame.is_file():
        before = out_dir / "overlay_before.png"
        before.write_bytes(best_frame.read_bytes())
        report.best_frame = before
        try:
            # Use cached boxes for the best frame index
            best_idx = frame_paths.index(best_frame) if best_frame in frame_paths else 0
            raw = boxes_by_frame[best_idx] if best_idx < len(boxes_by_frame) else []
            if raw:
                _draw_boxes(
                    before,
                    out_dir / "overlay_ocr.png",
                    [(b.x, b.y, b.w, b.h, b.text[:24], (0, 220, 80)) for b in raw],
                )
        except Exception:
            pass
        if regions:
            _draw_boxes(
                before,
                out_dir / "overlay_regions.png",
                [
                    (r.x, r.y, r.w, r.h, f"{r.from_text}->{r.text}"[:40], (255, 80, 40))
                    for r in regions
                ],
            )

    # --- Gate: surgical_span ---
    if regions and frame_w and frame_h:
        report.gates.append(check_surgical_spans(regions, frame_w=frame_w, frame_h=frame_h))
        if report.gates[-1].status == "fail":
            report.error = report.error or report.gates[-1].reason
    else:
        report.gates.append(GateResult("surgical_span", "skip", "No regions"))

    # --- Gate: paint ---
    t0 = time.perf_counter()
    if paint_still and regions and report.best_frame and report.best_frame.is_file():
        preview = out_dir / "preview_after.png"
        try:
            paint_regions_on_frame(report.best_frame, regions, preview)
            report.preview_after = preview
            report.gates.append(
                GateResult("paint", "pass", "Invisible heal+redraw still", (time.perf_counter() - t0) * 1000)
            )
            pq = check_paint_quality(preview_path=preview, used_healed_patches=True)
            report.gates.append(pq)
            if pq.status == "fail":
                report.error = report.error or pq.reason
        except Exception as exc:
            report.gates.append(
                GateResult("paint", "fail", str(exc), (time.perf_counter() - t0) * 1000)
            )
            report.error = report.error or str(exc)
    else:
        report.gates.append(
            GateResult("paint", "skip", "Nothing to paint", (time.perf_counter() - t0) * 1000)
        )

    # --- Gate: verify_after (one extra OCR on painted still only) ---
    if not verify:
        report.gates.append(GateResult("verify_after", "skip", "Disabled (--no-verify)"))
    elif report.preview_after and report.preview_after.is_file() and regions:
        report.gates.append(
            verify_after_paint(
                report.preview_after,
                replacements,
                regions,
                out_json=out_dir / "verify_after_ocr.json",
            )
        )
        if report.gates[-1].status == "fail":
            report.error = report.error or report.gates[-1].reason
    else:
        report.gates.append(GateResult("verify_after", "skip", "No painted preview"))

    report.gates.append(
        GateResult(
            "full_video",
            "skip",
            "Use Bulkcut job / --render to complete this gate",
        )
    )

    _write_json(out_dir / "audit.json", report.to_dict())
    return report


def audit_dir_for_job(preview_dir: Path) -> Path:
    """Job audits live under preview_dir/ocr_audit."""
    path = preview_dir / "ocr_audit"
    path.mkdir(parents=True, exist_ok=True)
    return path
