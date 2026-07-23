from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, replace as dc_replace
from functools import lru_cache
from pathlib import Path

from app.api.schemas.edits import EditInstructions, WatermarkPosition
from app.config import get_settings
from app.core.logging import get_logger
from app.services.ffmpeg_kit import enable_between
from app.services.ocr_audit import audit_dir_for_job, write_audit_snapshot
from app.services.scene_detect import (
    crop_similarity,
    detect_shots,
    refine_presence_window,
    shot_sample_times,
)
from app.services.storage import StorageService
from app.services.text_heal import (
    HealedPatch,
    inpaint_video_under_mask,
    paint_invisible_preview,
    write_heal_artifacts,
)
from app.services.text_ocr import RenderRegion, ocr_best_regions_for_replacements
from app.services.timeline_service import regions_to_template
from app.services.tracking_service import Detection, track_detections
from app.services.vision_service import detect_logos_and_graphics


logger = get_logger(__name__)

POSITION_EXPR = {
    WatermarkPosition.top_left: "10:10",
    WatermarkPosition.top_right: "W-w-10:10",
    WatermarkPosition.bottom_left: "10:H-h-10",
    WatermarkPosition.bottom_right: "W-w-10:H-h-10",
    WatermarkPosition.center: "(W-w)/2:(H-h)/2",
}


@dataclass
class RenderResult:
    occurrences: int
    preview_before: Path | None
    preview_after: Path | None
    template_json: str | None = None


@lru_cache
def detect_system_font(*, bold: bool = False) -> str | None:
    """Find a usable TrueType font for FFmpeg drawtext.

    Prefers bundled FONTS_DIR /assets/fonts, then OS fonts.
    """
    settings = get_settings()
    fonts_dir = settings.resolved_fonts_dir
    bundled = []
    if fonts_dir.is_dir():
        if bold:
            bundled = [
                fonts_dir / "DejaVuSans-Bold.ttf",
                fonts_dir / "LiberationSans-Bold.ttf",
                fonts_dir / "Arial-Bold.ttf",
                fonts_dir / "arialbd.ttf",
            ]
        else:
            bundled = [
                fonts_dir / "DejaVuSans.ttf",
                fonts_dir / "LiberationSans-Regular.ttf",
                fonts_dir / "Arial.ttf",
                fonts_dir / "arial.ttf",
            ]
        for path in bundled:
            if path.is_file():
                return str(path)

    if bold:
        bold_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
        ]
        windir = os.environ.get("WINDIR")
        if windir:
            bold_candidates.append(str(Path(windir) / "Fonts" / "arialbd.ttf"))
        for path in bold_candidates:
            if Path(path).is_file():
                return path
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
    ]
    windir = os.environ.get("WINDIR")
    if windir:
        candidates.append(str(Path(windir) / "Fonts" / "arial.ttf"))
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


class FFmpegService:
    def __init__(self, storage: StorageService | None = None) -> None:
        self.settings = get_settings()
        self.storage = storage or StorageService()

    def _font_opt(self, *, bold: bool = False, fontfile: str | None = None) -> str:
        if fontfile and Path(fontfile).is_file():
            font = fontfile
        else:
            configured = self.settings.ffmpeg_fontfile.strip()
            if configured and not bold:
                font = configured
            else:
                font = detect_system_font(bold=bold) or detect_system_font(bold=False) or configured or ""
        if not font:
            return ""
        font_path = font.replace("\\", "/").replace(":", "\\:")
        return f":fontfile='{font_path}'"


    async def probe_duration(self, input_path: Path) -> float | None:
        cmd = [
            self.settings.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            return float(stdout.decode().strip())
        except Exception:
            return None

    async def probe_video_size(self, input_path: Path) -> tuple[int, int] | None:
        cmd = [
            self.settings.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(input_path),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return None
            raw = stdout.decode().strip()
            if "x" not in raw:
                return None
            w_s, h_s = raw.split("x", 1)
            return int(w_s), int(h_s)
        except Exception:
            return None

    async def _mark_moving_regions(
        self,
        input_path: Path,
        regions: list[RenderRegion],
        work_dir: Path,
    ) -> list[RenderRegion]:
        """Sample 3 frames per region; force removelogo when bbox IoU drops ≤ 0.9."""
        if not regions:
            return regions
        out: list[RenderRegion] = []
        for r in regions:
            t0 = float(r.t_start if r.t_start is not None else 0.0)
            t1 = float(r.t_end if r.t_end is not None else t0 + 1.0)
            if t1 <= t0:
                t1 = t0 + 0.5
            span = t1 - t0
            sample_ts = [t0 + span * f for f in (0.25, 0.5, 0.75)]
            dets: list[Detection] = []
            for fi, t in enumerate(sample_ts):
                frame_path = work_dir / f"stab_{id(r)}_{fi}.png"
                ok = await self._extract_frame(input_path, frame_path, at_seconds=t)
                if not ok:
                    continue
                # Presence via crop similarity against the known bbox (no full OCR).
                try:
                    from PIL import Image as _PILImage

                    im = _PILImage.open(frame_path).convert("RGB")
                    pad = 8
                    x0 = max(0, r.x - pad)
                    y0 = max(0, r.y - pad)
                    x1 = min(im.size[0], r.x + r.w + pad)
                    y1 = min(im.size[1], r.y + r.h + pad)
                    # Use original bbox as the detection when crop still matches itself
                    # (static) — motion shows up as failed similarity vs the mid sample.
                    dets.append(
                        Detection(
                            frame_idx=fi,
                            t=t,
                            bbox=(r.x, r.y, r.w, r.h),
                            label=r.from_text or "",
                            score=1.0,
                        )
                    )
                    im.close()
                except Exception:
                    continue

            if len(dets) < 2:
                out.append(r)
                continue

            # Compare mid-frame crop to side-frame crops at the same bbox.
            # If either side fails similarity, treat as moving/animating.
            mid_path = work_dir / f"stab_{id(r)}_1.png"
            side_paths = [work_dir / f"stab_{id(r)}_0.png", work_dir / f"stab_{id(r)}_2.png"]
            moving = False
            try:
                from PIL import Image as _PILImage2

                if mid_path.is_file():
                    mid = _PILImage2.open(mid_path).convert("RGB")
                    pad = 4
                    box = (
                        max(0, r.x - pad),
                        max(0, r.y - pad),
                        min(mid.size[0], r.x + r.w + pad),
                        min(mid.size[1], r.y + r.h + pad),
                    )
                    ref = mid.crop(box)
                    mid.close()
                    for sp in side_paths:
                        if not sp.is_file():
                            moving = True
                            break
                        side = _PILImage2.open(sp).convert("RGB")
                        probe = side.crop(
                            (
                                max(0, r.x - pad),
                                max(0, r.y - pad),
                                min(side.size[0], r.x + r.w + pad),
                                min(side.size[1], r.y + r.h + pad),
                            )
                        )
                        side.close()
                        if not crop_similarity(ref, probe, threshold=0.9):
                            moving = True
                            break
            except Exception:
                moving = True

            # Also run IoU tracker across detections (same bbox → IoU=1; if we
            # later attach shifted boxes this catches motion).
            tracks = track_detections(dets, iou_threshold=0.3)
            if tracks:
                bboxes = [d.bbox for d in tracks[0].detections]
                for i in range(len(bboxes) - 1):
                    from app.services.tracking_service import _iou

                    if _iou(bboxes[i], bboxes[i + 1]) <= 0.9:
                        moving = True
                        break

            if moving:
                logger.info(
                    "Region %r unstable across window — routing to per-frame removelogo",
                    r.from_text,
                )
                out.append(dc_replace(r, heal_mode="inpaint"))
            else:
                out.append(r)
        return out

    async def locate_text_regions(
        self,
        input_path: Path,
        replacements: list,
        *,
        preview_dir: Path | None = None,
        progress_cb=None,
    ) -> tuple[list[RenderRegion], Path | None, float]:
        """OCR sample frames (one per shot) and return paint regions + preview.

        Returns (regions, preview_before_path, sample_time_seconds).
        Regions carry t_start/t_end from the shot where text was found.
        """
        duration = await self.probe_duration(input_path)
        max_shots = self.settings.max_shots_sampled
        if float(self.settings.test_clip_seconds or 0.0) > 0:
            max_shots = min(max_shots, 3)
        try:
            shots = await asyncio.to_thread(detect_shots, str(input_path))
        except Exception as exc:
            logger.warning("detect_shots failed: %s", exc)
            shots = [(0.0, float(duration or 10.0))]

        samples = shot_sample_times(shots, max_shots=max_shots)
        if not samples:
            samples = [(1.0, 0.0, float(duration or 10.0))]

        candidates = [s[0] for s in samples]
        sample_at = candidates[0]
        preview_before: Path | None = None

        with tempfile.TemporaryDirectory(prefix="vgai_frame_") as tmp:
            frame_paths: list[Path] = []
            for idx, at in enumerate(candidates):
                frame_path = Path(tmp) / f"sample_{idx}.png"
                if await self._extract_frame(input_path, frame_path, at_seconds=at):
                    frame_paths.append(frame_path)
                if progress_cb:
                    pct = 12.0 + (idx + 1) / max(1, len(candidates)) * 6.0
                    await progress_cb(min(18.0, pct))
            if not frame_paths:
                raise RuntimeError("Failed to extract sample frames for OCR")

            if progress_cb:
                await progress_cb(19.0)

            ocr_error: str | None = None
            try:
                text_regions, best_frame = await asyncio.to_thread(
                    ocr_best_regions_for_replacements,
                    frame_paths,
                    replacements,
                )
            except Exception as exc:
                ocr_error = str(exc)
                text_regions, best_frame = [], None
                if preview_dir is not None:
                    preview_dir.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(
                        write_audit_snapshot,
                        audit_dir_for_job(preview_dir),
                        replacements=replacements,
                        frame_paths=frame_paths,
                        sample_times=candidates,
                        regions=[],
                        best_frame=frame_paths[0] if frame_paths else None,
                        error=ocr_error,
                        dump_ocr=False,
                        paint_still=False,
                    )
                raise

            # Per-region presence windows (not one shared shot window).
            # Hint time: best_frame sample, else mid of first shot window.
            if best_frame is not None:
                try:
                    hint_idx = frame_paths.index(best_frame)
                    hint_t = candidates[hint_idx]
                except ValueError:
                    hint_t = candidates[0] if candidates else 1.0
            else:
                hint_t = candidates[0] if candidates else 1.0

            dur = float(duration or 10.0)
            refined: list[RenderRegion] = []
            for r in text_regions:
                # Prefer the region's own times if already set (e.g. template re-render)
                if r.t_start is not None and r.t_end is not None and r.t_end > r.t_start:
                    refined.append(r)
                    continue

                # Reference crop from best available sample frame
                ref_frame_path = best_frame or frame_paths[0]
                try:
                    from PIL import Image as _PILImage

                    ref_im = _PILImage.open(ref_frame_path).convert("RGB")
                    pad = 4
                    x0 = max(0, r.x - pad)
                    y0 = max(0, r.y - pad)
                    x1 = min(ref_im.size[0], r.x + r.w + pad)
                    y1 = min(ref_im.size[1], r.y + r.h + pad)
                    ref_crop = ref_im.crop((x0, y0, x1, y1))
                    ref_im.close()
                except Exception:
                    refined.append(
                        RenderRegion(
                            x=r.x,
                            y=r.y,
                            w=r.w,
                            h=r.h,
                            fill_rgb=r.fill_rgb,
                            font_rgb=r.font_rgb,
                            fontsize=r.fontsize,
                            text=r.text,
                            align=r.align,
                            from_text=r.from_text,
                            ocr_text=r.ocr_text,
                            bold=r.bold,
                            baseline_y=r.baseline_y,
                            fontfile=r.fontfile,
                            text_y=r.text_y,
                            t_start=0.0,
                            t_end=dur,
                            entity_id=r.entity_id,
                            heal_mode=r.heal_mode,
                        )
                    )
                    continue

                probe_cache: dict[float, bool] = {}
                # OCR already matched on the hint frame — don't re-probe it
                # (ffmpeg -ss re-seek can differ enough to fail crop_similarity).
                probe_cache[round(hint_t, 3)] = True

                def _is_present(t: float, _ref=ref_crop, _box=(x0, y0, x1, y1)) -> bool:
                    key = round(t, 3)
                    if key in probe_cache:
                        return probe_cache[key]
                    # Sync extract into tmp for presence probe
                    probe_path = Path(tmp) / f"presence_{key:.3f}.png".replace(".", "_")
                    ok = False
                    try:
                        import subprocess

                        proc = subprocess.run(
                            [
                                self.settings.ffmpeg_path,
                                "-y",
                                "-ss",
                                str(max(0.0, t)),
                                "-i",
                                str(input_path),
                                "-frames:v",
                                "1",
                                "-update",
                                "1",
                                str(probe_path),
                            ],
                            capture_output=True,
                            check=False,
                        )
                        ok = proc.returncode == 0 and probe_path.is_file()
                    except Exception:
                        ok = False
                    if not ok:
                        probe_cache[key] = False
                        return False
                    try:
                        from PIL import Image as _PILImage2

                        im = _PILImage2.open(probe_path).convert("RGB")
                        crop = im.crop(_box)
                        im.close()
                        # Lower threshold: B-roll behind static banners changes a lot
                        present = crop_similarity(_ref, crop, threshold=0.35)
                    except Exception:
                        present = False
                    probe_cache[key] = present
                    return present

                t0, t1 = refine_presence_window(hint_t, dur, _is_present, step=0.5, max_probes=12)
                # Burned-in promo overlays often span most of the clip. If presence
                # collapsed to a tiny window, prefer full duration over missing edits.
                if (t1 - t0) < max(2.5, dur * 0.05):
                    logger.warning(
                        "Region %r presence window too tight [%.2f,%.2f]; using full duration",
                        r.from_text,
                        t0,
                        t1,
                    )
                    t0, t1 = 0.0, dur
                refined.append(
                    RenderRegion(
                        x=r.x,
                        y=r.y,
                        w=r.w,
                        h=r.h,
                        fill_rgb=r.fill_rgb,
                        font_rgb=r.font_rgb,
                        fontsize=r.fontsize,
                        text=r.text,
                        align=r.align,
                        from_text=r.from_text,
                        ocr_text=r.ocr_text,
                        bold=r.bold,
                        baseline_y=r.baseline_y,
                        fontfile=r.fontfile,
                        text_y=r.text_y,
                        t_start=t0,
                        t_end=t1,
                        entity_id=r.entity_id,
                        heal_mode=r.heal_mode,
                    )
                )
                logger.info(
                    "Region %r presence window=[%.2f,%.2f] (hint=%.2f)",
                    r.from_text,
                    t0,
                    t1,
                    hint_t,
                )

            text_regions = refined
            logger.info("OCR located %d text region(s) with per-region windows", len(text_regions))

            if preview_dir is not None:
                preview_dir.mkdir(parents=True, exist_ok=True)
                if best_frame is not None:
                    preview_before = preview_dir / "preview_before.png"
                    preview_before.write_bytes(best_frame.read_bytes())
                    try:
                        idx = frame_paths.index(best_frame)
                        sample_at = candidates[idx]
                    except ValueError:
                        pass
                await asyncio.to_thread(
                    write_audit_snapshot,
                    audit_dir_for_job(preview_dir),
                    replacements=replacements,
                    frame_paths=frame_paths,
                    sample_times=candidates,
                    regions=text_regions,
                    best_frame=best_frame,
                    dump_ocr=False,
                    paint_still=True,
                )

        if progress_cb:
            await progress_cb(22.0)
        return text_regions, preview_before, sample_at

    def _escape_drawtext(self, text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace("%", "\\%")
            .replace("&", "\\&")
        )

    @staticmethod
    def _rgb_hex(rgb: tuple[int, int, int]) -> str:
        r, g, b = rgb
        return f"0x{r:02X}{g:02X}{b:02X}"

    async def _extract_frame(self, input_path: Path, dest: Path, at_seconds: float = 1.0) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-ss",
            str(max(0.0, at_seconds)),
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0 and dest.is_file()

    async def _mux_audio(self, video_path: Path, audio_src: Path, dest: Path) -> bool:
        """Copy video stream from video_path and audio from audio_src."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_src),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0 and dest.is_file()

    def build_filter_complex(
        self,
        instructions: EditInstructions,
        logo_path: Path | None,
        watermark_path: Path | None,
        text_regions: list[RenderRegion] | None = None,
        healed_patches: list[HealedPatch] | None = None,
        removelogo_mask: Path | None = None,
    ) -> tuple[list[str], str, str]:
        """Build filter graph. Returns (extra_inputs, filter_str, final_label)."""
        inputs: list[str] = []
        filters: list[str] = []
        current = "[0:v]"
        input_index = 1
        regions = text_regions or []
        patches = healed_patches or []

        if instructions.replace_text:
            if not regions:
                # No silent fallback band — OCR must find the text.
                raise ValueError(
                    "No OCR text regions to replace. "
                    "The requested text was not found on sampled frames."
                )
            # Invisible heal:
            # - video-bg glyphs: removelogo (per-frame) via mask
            # - banner glyphs: static soft-fill RGBA overlays
            # Never use opaque drawbox stamps.
            if removelogo_mask is not None and removelogo_mask.is_file():
                mask_esc = str(removelogo_mask.resolve()).replace("\\", "/").replace(":", "\\:")
                label = "[rl]"
                filters.append(f"{current}removelogo=filename='{mask_esc}'{label}")
                current = label

            for i, patch in enumerate(patches):
                # Static soft-fill overlays for banner/flat plates; inpaint is per-frame.
                if getattr(patch, "mode", "banner") == "inpaint":
                    continue
                if not patch.path.is_file():
                    continue
                # Explicit loop so 1-frame PNG overlays don't rely on eof_action defaults.
                inputs.extend(["-loop", "1", "-i", str(patch.path)])
                label = f"[hp{i}]"
                en = enable_between(
                    getattr(patch, "t_start", None),
                    getattr(patch, "t_end", None),
                )
                filters.append(
                    f"{current}[{input_index}:v]overlay={patch.x}:{patch.y}:format=auto{en}{label}"
                )
                current = label
                input_index += 1

            for i, region in enumerate(regions):
                label = f"[t{i}]"
                text = self._escape_drawtext(region.text)
                fontcolor = self._rgb_hex(region.font_rgb)
                font_opt = self._font_opt(
                    bold=getattr(region, "bold", False),
                    fontfile=getattr(region, "fontfile", None),
                )
                if getattr(region, "text_y", 0):
                    text_y = region.text_y
                else:
                    text_y = region.y + max(0, (region.h - region.fontsize) // 2)
                if region.align == "center":
                    text_x = f"{region.x}+({region.w}-text_w)/2"
                else:
                    text_x = str(region.x + 2)
                en = enable_between(getattr(region, "t_start", None), getattr(region, "t_end", None))
                filters.append(
                    f"{current}drawtext=text='{text}':fontsize={region.fontsize}:"
                    f"fontcolor={fontcolor}:x={text_x}:y={text_y}{font_opt}{en}{label}"
                )
                current = label

        if instructions.replace_logo and logo_path and logo_path.is_file():
            inputs.extend(["-i", str(logo_path)])
            label = "[logo]"
            # Logo overlays use full duration unless template entity supplies a window later
            filters.append(
                f"[{input_index}:v]scale=iw*0.15:-1[lg];{current}[lg]overlay=20:20{label}"
            )
            current = label
            input_index += 1

        wm_path = watermark_path
        if instructions.watermark is not None:
            if wm_path is None and logo_path is not None:
                wm_path = logo_path
            if wm_path and wm_path.is_file():
                inputs.extend(["-i", str(wm_path)])
                pos = POSITION_EXPR[instructions.watermark]
                label = "[wm]"
                filters.append(
                    f"[{input_index}:v]scale=iw*0.12:-1[wms];{current}[wms]overlay={pos}{label}"
                )
                current = label
                input_index += 1
            else:
                label = "[wm]"
                font_opt = self._font_opt(bold=False)
                filters.append(
                    f"{current}drawtext=text='WATERMARK':fontsize=24:fontcolor=white@0.6:"
                    f"x=w-tw-20:y=h-th-20{font_opt}{label}"
                )
                current = label

        if not filters:
            return inputs, "", "0:v"

        # Strip brackets for -map (FFmpeg wants 0:v or t0, not [t0] in some forms;
        # we keep bracketed label for -map which accepts [label]).
        final_label = current.strip()
        if not final_label.startswith("["):
            final_label = f"[{final_label}]"
        return inputs, ";".join(filters), final_label

    async def _maybe_trim_test_clip(self, input_path: Path, work_dir: Path) -> Path:
        """When TEST_CLIP_SECONDS > 0, trim to the first N seconds and return that path."""
        n = float(self.settings.test_clip_seconds or 0.0)
        if n <= 0:
            return input_path
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "test_clip.mp4"
        logger.info("TEST_CLIP_SECONDS=%s — working on first %.1fs only", n, n)

        async def _run(cmd: list[str]) -> bool:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 64:
                logger.warning(
                    "test clip trim failed (code=%s): %s",
                    proc.returncode,
                    (stderr or b"").decode(errors="ignore")[-400:],
                )
                if out.is_file():
                    try:
                        out.unlink()
                    except OSError:
                        pass
                return False
            return True

        copy_cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-ss",
            "0",
            "-t",
            str(n),
            "-i",
            str(input_path),
            "-c",
            "copy",
            str(out),
        ]
        if await _run(copy_cmd):
            return out

        reenc_cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-ss",
            "0",
            "-t",
            str(n),
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ]
        if await _run(reenc_cmd):
            return out
        logger.warning("TEST_CLIP_SECONDS trim failed; continuing with full input")
        return input_path

    async def _build_template_json(
        self,
        *,
        resolved_regions: list[RenderRegion],
        input_path: Path,
        duration: float | None,
        preview_before: Path | None,
        healed_patches: list[HealedPatch],
        preview_dir: Path | None,
    ) -> str | None:
        if not resolved_regions:
            return None
        size = await self.probe_video_size(input_path)
        width, height = size if size else (1080, 1920)
        dur = float(duration or 10.0)
        logo_ents = []
        if preview_before and preview_before.is_file():
            try:
                t0 = min((r.t_start or 0.0) for r in resolved_regions)
                t1 = max((r.t_end or dur) for r in resolved_regions)
                logo_ents = await asyncio.to_thread(
                    detect_logos_and_graphics,
                    preview_before,
                    t_start=t0,
                    t_end=t1,
                )
            except Exception as exc:
                logger.info("Vision detect skipped: %s", exc)
        template = regions_to_template(
            resolved_regions,
            duration=dur,
            width=width,
            height=height,
            logo_entities=logo_ents or None,
        )
        mode_by_text = {p.from_text: p.mode for p in healed_patches}
        ents = []
        for ent in template.entities:
            if ent.text and ent.text in mode_by_text:
                ents.append(ent.model_copy(update={"inpaint_mode": mode_by_text.get(ent.text)}))
            else:
                matched_mode = None
                for r in resolved_regions:
                    if r.entity_id == ent.id or r.text == ent.text:
                        matched_mode = "flat"
                        break
                for p in healed_patches:
                    if p.text == ent.text or p.from_text:
                        matched_mode = "lama" if p.mode == "inpaint" else p.mode
                        break
                ents.append(
                    ent.model_copy(update={"inpaint_mode": matched_mode or ent.inpaint_mode})
                )
        template = template.model_copy(update={"entities": ents})
        template_json = template.model_dump_json()
        if preview_dir is not None:
            preview_dir.mkdir(parents=True, exist_ok=True)
            (preview_dir / "template.json").write_text(template_json, encoding="utf-8")
        return template_json

    async def render(
        self,
        input_path: Path,
        output_path: Path,
        instructions: EditInstructions,
        upload_id: str,
        progress_cb=None,
        preview_dir: Path | None = None,
        text_regions: list[RenderRegion] | None = None,
    ) -> RenderResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()

        logo_path = None
        watermark_path = None
        if instructions.replace_logo:
            logo_path = self.storage.resolve_asset(upload_id, instructions.replace_logo)
        if instructions.watermark_image:
            watermark_path = self.storage.resolve_asset(upload_id, instructions.watermark_image)
        elif instructions.replace_logo:
            watermark_path = logo_path

        preview_before: Path | None = None
        preview_after: Path | None = None
        sample_at_for_preview = 1.0
        # Allow callers (audit CLI) to supply regions and skip a second EasyOCR pass.
        resolved_regions: list[RenderRegion] = list(text_regions or [])
        heal_source: Path | None = None

        work_dir = preview_dir or output_path.parent
        input_path = await self._maybe_trim_test_clip(input_path, work_dir)

        duration = await self.probe_duration(input_path)

        if instructions.replace_text and not resolved_regions:
            resolved_regions, preview_before, sample_at_for_preview = await self.locate_text_regions(
                input_path,
                instructions.replace_text,
                preview_dir=preview_dir,
                progress_cb=progress_cb,
            )
            heal_source = preview_before
            logger.info(
                "OCR located %d text region(s) for in-place replace",
                len(resolved_regions),
            )

        # Invisible heal patches for text replace (no opaque drawbox)
        healed_patches: list[HealedPatch] = []
        removelogo_mask: Path | None = None
        if instructions.replace_text and resolved_regions:
            # Skip motion forcing in short test clips — keep static banner heal.
            if float(self.settings.test_clip_seconds or 0.0) <= 0:
                stab_dir = work_dir / "heal_stab"
                stab_dir.mkdir(parents=True, exist_ok=True)
                resolved_regions = await self._mark_moving_regions(
                    input_path, resolved_regions, stab_dir
                )
            if heal_source is None or not heal_source.is_file():
                heal_dir = work_dir
                heal_dir.mkdir(parents=True, exist_ok=True)
                heal_source = heal_dir / "heal_source.png"
                # Prefer mid-window of first region when available
                at = sample_at_for_preview
                if resolved_regions:
                    r0 = resolved_regions[0]
                    if r0.t_start is not None and r0.t_end is not None:
                        at = (float(r0.t_start) + float(r0.t_end)) / 2.0
                ok = await self._extract_frame(input_path, heal_source, at_seconds=at)
                if not ok:
                    raise RuntimeError("Failed to extract frame for text heal")
                if preview_before is None:
                    preview_before = heal_source
            patch_dir = work_dir / "heal_patches"
            healed_patches, removelogo_mask = await asyncio.to_thread(
                write_heal_artifacts,
                heal_source,
                resolved_regions,
                patch_dir,
            )

        # Persist EditableTemplate early (before expensive inpaint/encode).
        template_json = await self._build_template_json(
            resolved_regions=resolved_regions,
            input_path=input_path,
            duration=duration,
            preview_before=preview_before,
            healed_patches=healed_patches,
            preview_dir=preview_dir,
        )

        if self.settings.template_only:
            logger.info("TEMPLATE_ONLY=true — skipping video inpaint and final encode")
            if preview_dir is not None:
                preview_dir.mkdir(parents=True, exist_ok=True)
                if preview_before is not None and preview_before.is_file():
                    dest_before = preview_dir / "preview_before.png"
                    if preview_before.resolve() != dest_before.resolve():
                        try:
                            from shutil import copy2

                            copy2(preview_before, dest_before)
                            preview_before = dest_before
                        except OSError:
                            pass
                if (
                    preview_before is not None
                    and preview_before.is_file()
                    and resolved_regions
                ):
                    preview_after = preview_dir / "preview_after.png"
                    await asyncio.to_thread(
                        paint_invisible_preview,
                        preview_before,
                        resolved_regions,
                        preview_after,
                        patches=healed_patches or None,
                    )
            if progress_cb:
                await progress_cb(100.0)
            return RenderResult(
                occurrences=len(resolved_regions),
                preview_before=preview_before if preview_before and preview_before.is_file() else None,
                preview_after=preview_after if preview_after and preview_after.is_file() else None,
                template_json=template_json,
            )

        # Video-bg glyphs: per-frame OpenCV inpaint (avoids frozen/removelogo plates).
        # Banner glyphs stay as static soft-fill overlays in the filter graph.
        render_input = input_path
        if removelogo_mask is not None and removelogo_mask.is_file():
            inpainted = work_dir / "video_inpainted.mp4"
            await asyncio.to_thread(
                inpaint_video_under_mask,
                input_path,
                removelogo_mask,
                inpainted,
            )
            # Attach original audio onto the OpenCV intermediate
            muxed = work_dir / "video_inpainted_a.mp4"
            mux_ok = await self._mux_audio(inpainted, input_path, muxed)
            render_input = muxed if mux_ok else inpainted
            removelogo_mask = None  # already applied per-frame

        extra_inputs, filter_complex, final_label = self.build_filter_complex(
            instructions,
            logo_path,
            watermark_path,
            text_regions=resolved_regions,
            healed_patches=healed_patches,
            removelogo_mask=removelogo_mask,
        )

        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-i",
            str(render_input),
            *extra_inputs,
        ]

        if filter_complex:
            cmd.extend(["-filter_complex", filter_complex, "-map", final_label])
        else:
            cmd.extend(["-map", "0:v"])

        vcodec = "libx264"
        vextra: list[str] = ["-preset", "veryfast", "-crf", "23"]
        hw = (self.settings.hwaccel or "").strip().lower()
        if hw == "nvenc":
            vcodec = "h264_nvenc"
            vextra = ["-preset", "p4", "-cq", "23"]
        elif hw == "qsv":
            vcodec = "h264_qsv"
            vextra = ["-global_quality", "23"]

        cmd.extend(
            [
                "-map",
                "0:a?",
                "-c:v",
                vcodec,
                *vextra,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output_path),
            ]
        )

        logger.info("FFmpeg cmd: %s", " ".join(shlex.quote(c) for c in cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_lines: list[str] = []
        time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")

        assert proc.stderr is not None
        while True:
            line_b = await proc.stderr.readline()
            if not line_b:
                break
            line = line_b.decode(errors="ignore").strip()
            if line:
                stderr_lines.append(line)
            if progress_cb and duration:
                m = time_re.search(line)
                if m:
                    h, mi, s = m.groups()
                    seconds = int(h) * 3600 + int(mi) * 60 + float(s)
                    pct = max(0.0, min(99.0, (seconds / duration) * 100))
                    await progress_cb(pct)

        code = await proc.wait()
        if code != 0 or not output_path.is_file():
            tail = "\n".join(stderr_lines[-40:])
            raise RuntimeError(f"FFmpeg failed (code={code}): {tail}")

        if preview_dir is not None:
            preview_dir.mkdir(parents=True, exist_ok=True)
            if preview_before is None:
                preview_before = preview_dir / "preview_before.png"
                await self._extract_frame(input_path, preview_before, at_seconds=sample_at_for_preview)
            preview_after = preview_dir / "preview_after.png"
            await self._extract_frame(output_path, preview_after, at_seconds=sample_at_for_preview)

            # Append full_video + verify gates onto job OCR audit when text was replaced
            if instructions.replace_text and resolved_regions:
                audit_path = audit_dir_for_job(preview_dir) / "audit.json"
                if audit_path.is_file():
                    try:
                        from app.services.ocr_audit import verify_after_paint

                        data = json.loads(audit_path.read_text(encoding="utf-8"))
                        gates = data.setdefault("gates", [])
                        # full_video
                        gates = [g for g in gates if g.get("name") not in ("full_video", "verify_after")]
                        gates.append(
                            {
                                "name": "full_video",
                                "status": "pass",
                                "reason": f"occurrences={len(resolved_regions)}",
                                "elapsed_ms": 0,
                                "details": {"occurrences": len(resolved_regions), "output": str(output_path)},
                            }
                        )
                        if preview_after.is_file():
                            v = await asyncio.to_thread(
                                verify_after_paint,
                                preview_after,
                                instructions.replace_text,
                                resolved_regions,
                                out_json=audit_dir_for_job(preview_dir) / "verify_after_ocr.json",
                            )
                            gates.append(v.to_dict())
                        data["gates"] = gates
                        data["ok"] = all(
                            g.get("status") in ("pass", "skip", "warn") for g in gates
                        )
                        audit_path.write_text(
                            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                        )
                    except Exception as exc:
                        logger.warning("Failed to update OCR audit after render: %s", exc)

        if progress_cb:
            await progress_cb(100.0)

        return RenderResult(
            occurrences=len(resolved_regions),
            preview_before=preview_before if preview_before and preview_before.is_file() else None,
            preview_after=preview_after if preview_after and preview_after.is_file() else None,
            template_json=template_json,
        )

    async def check_available(self) -> bool:
        """Return True if ffmpeg binary runs. Uses a thread so Windows + reload works."""
        def _probe() -> bool:
            import subprocess

            try:
                proc = subprocess.run(
                    [self.settings.ffmpeg_path, "-version"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                return proc.returncode == 0
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                return False

        try:
            return await asyncio.to_thread(_probe)
        except Exception:
            return False
