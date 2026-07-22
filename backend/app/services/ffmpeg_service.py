from __future__ import annotations

import asyncio
import os
import re
import shlex
import tempfile
from functools import lru_cache
from pathlib import Path

from app.api.schemas.edits import EditInstructions, WatermarkPosition
from app.config import get_settings
from app.core.logging import get_logger
from app.services.storage import StorageService
from app.services.text_locate import TextRegion, pick_regions_for_replacements

logger = get_logger(__name__)

POSITION_EXPR = {
    WatermarkPosition.top_left: "10:10",
    WatermarkPosition.top_right: "W-w-10:10",
    WatermarkPosition.bottom_left: "10:H-h-10",
    WatermarkPosition.bottom_right: "W-w-10:H-h-10",
    WatermarkPosition.center: "(W-w)/2:(H-h)/2",
}


@lru_cache
def detect_system_font() -> str | None:
    """Find a usable TrueType font for FFmpeg drawtext on Windows/macOS/Linux."""
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    windir = os.environ.get("WINDIR")
    if windir:
        candidates.insert(0, str(Path(windir) / "Fonts" / "arial.ttf"))
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


class FFmpegService:
    def __init__(self, storage: StorageService | None = None) -> None:
        self.settings = get_settings()
        self.storage = storage or StorageService()

    def _font_opt(self) -> str:
        font = self.settings.ffmpeg_fontfile.strip() or detect_system_font() or ""
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

    def _escape_drawtext(self, text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace("%", "\\%")
            .replace("&", "\\&")
        )

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

    def build_filter_complex(
        self,
        instructions: EditInstructions,
        logo_path: Path | None,
        watermark_path: Path | None,
        text_regions: list[TextRegion] | None = None,
    ) -> tuple[list[str], str]:
        inputs: list[str] = []
        filters: list[str] = []
        current = "[0:v]"
        input_index = 1
        font_opt = self._font_opt()
        regions = text_regions or []

        for i, pair in enumerate(instructions.replace_text):
            box_label = f"[tb{i}]"
            label = f"[t{i}]"
            text = self._escape_drawtext(pair.to)
            region = regions[i] if i < len(regions) else None

            if region is not None:
                # Cover only the detected date/text line inside the existing banner,
                # then redraw replacement text in that same slot.
                r, g, b = region.fill_rgb
                color = f"0x{r:02X}{g:02X}{b:02X}"
                text_y = region.y + max(0, (region.h - region.fontsize) // 2)
                filters.append(
                    f"{current}drawbox=x={region.x}:y={region.y}:w={region.w}:h={region.h}:"
                    f"color={color}@1:t=fill{box_label}"
                )
                filters.append(
                    f"{box_label}drawtext=text='{text}':fontsize={region.fontsize}:"
                    f"fontcolor=white:x=(w-text_w)/2:y={text_y}{font_opt}{label}"
                )
            else:
                # Fallback: thin lower-third band if banner detection fails
                band_h = 48
                bottom_pad = (len(instructions.replace_text) - 1 - i) * band_h + 220
                filters.append(
                    f"{current}drawbox=x=80:y=ih-{band_h + bottom_pad}:w=iw-160:h={band_h}:"
                    f"color=0x0A2F42@1:t=fill{box_label}"
                )
                filters.append(
                    f"{box_label}drawtext=text='{text}':fontsize=32:fontcolor=white:"
                    f"x=(w-text_w)/2:y=h-th-{bottom_pad + 12}{font_opt}{label}"
                )
            current = label

        if instructions.replace_logo and logo_path and logo_path.is_file():
            inputs.extend(["-i", str(logo_path)])
            label = "[logo]"
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
                filters.append(
                    f"{current}drawtext=text='WATERMARK':fontsize=24:fontcolor=white@0.6:"
                    f"x=w-tw-20:y=h-th-20{font_opt}{label}"
                )
                current = label

        if not filters:
            return inputs, ""

        return inputs, ";".join(filters)

    async def render(
        self,
        input_path: Path,
        output_path: Path,
        instructions: EditInstructions,
        upload_id: str,
        progress_cb=None,
    ) -> None:
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

        text_regions: list[TextRegion] = []
        if instructions.replace_text:
            duration = await self.probe_duration(input_path)
            # Try a few sample times; keep the frame that yields the best date-line hit.
            if duration and duration > 3:
                candidates = [
                    min(max(duration * p, 0.5), max(duration - 0.5, 0.5))
                    for p in (0.12, 0.22, 0.35)
                ]
            else:
                candidates = [1.0]
            best: list[TextRegion] = []
            with tempfile.TemporaryDirectory(prefix="vgai_frame_") as tmp:
                for idx, sample_at in enumerate(candidates):
                    frame_path = Path(tmp) / f"sample_{idx}.png"
                    if not await self._extract_frame(input_path, frame_path, at_seconds=sample_at):
                        continue
                    try:
                        found = pick_regions_for_replacements(
                            frame_path, len(instructions.replace_text)
                        )
                    except Exception as exc:
                        logger.warning("Banner text locate failed at t=%.1f: %s", sample_at, exc)
                        continue
                    if len(found) > len(best):
                        best = found
                    elif len(found) == len(best) == len(instructions.replace_text):
                        # Prefer thinner (more text-line-like) boxes
                        if found and best and found[-1].h < best[-1].h:
                            best = found
                text_regions = best
                logger.info(
                    "Located %d banner text region(s) for in-place replace",
                    len(text_regions),
                )

        extra_inputs, filter_complex = self.build_filter_complex(
            instructions, logo_path, watermark_path, text_regions=text_regions
        )

        duration = await self.probe_duration(input_path)

        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            *extra_inputs,
        ]

        if filter_complex:
            last_label_match = re.findall(r"\[([^\]]+)\]", filter_complex)
            map_label = f"[{last_label_match[-1]}]" if last_label_match else "[0:v]"
            cmd.extend(["-filter_complex", filter_complex, "-map", map_label])
        else:
            cmd.extend(["-map", "0:v"])

        cmd.extend(
            [
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
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

        if progress_cb:
            await progress_cb(100.0)

    async def check_available(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.settings.ffmpeg_path,
                "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False
