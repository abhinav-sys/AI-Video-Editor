"""Shot boundary detection via PySceneDetect (with duration fallback)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


def detect_shots(path: str | Path, threshold: float = 27.0) -> list[tuple[float, float]]:
    """Return list of (t_start, t_end) seconds for each shot.

    Falls back to a single full-duration shot if detection fails or the
    video has no cuts.
    """
    video_path = Path(path)
    if not video_path.is_file():
        raise FileNotFoundError(str(video_path))

    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import ContentDetector
    except ImportError:
        logger.warning("scenedetect not installed; using full-duration shot")
        return _fallback_full(video_path)

    try:
        video = open_video(str(video_path))
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=threshold))
        sm.detect_scenes(video)
        scenes = sm.get_scene_list()
        if not scenes:
            return _fallback_full(video_path, duration_hint=float(video.duration.get_seconds()))
        return [(float(s.get_seconds()), float(e.get_seconds())) for s, e in scenes]
    except Exception as exc:
        logger.warning("Scene detection failed (%s); using full-duration shot", exc)
        return _fallback_full(video_path)


def shot_sample_times(
    shots: list[tuple[float, float]],
    *,
    max_shots: int = 12,
) -> list[tuple[float, float, float]]:
    """Pick representative sample time per shot: (sample_t, t_start, t_end).

    Caps to max_shots evenly spaced across the shot list.
    """
    if not shots:
        return [(1.0, 0.0, 1.0)]
    if len(shots) > max_shots:
        step = len(shots) / max_shots
        indices = [int(i * step) for i in range(max_shots)]
        selected = [shots[i] for i in indices]
    else:
        selected = shots

    # One continuous shot (common without scenedetect): sample several times
    # across the timeline so overlays that appear mid-clip are not missed.
    if len(selected) == 1 and max_shots > 1:
        start, end = selected[0]
        dur = max(0.1, end - start)
        if dur >= 2.5:
            n = min(max_shots, max(3, min(8, int(dur / 1.5) + 1)))
            out: list[tuple[float, float, float]] = []
            for i in range(n):
                # Evenly spaced interior samples (avoid exact 0 / end)
                frac = (i + 1) / (n + 1)
                sample = start + dur * frac
                out.append((sample, start, end))
            return out

    out = []
    for start, end in selected:
        mid = (start + end) / 2.0
        # Stay inside the shot with a small margin
        margin = min(0.25, max(0.0, (end - start) / 4))
        sample = min(max(mid, start + margin), max(end - margin, start))
        out.append((sample, start, end))
    return out


def refine_presence_window(
    t_hint: float,
    duration: float,
    is_present: Callable[[float], bool],
    *,
    step: float = 0.5,
    max_probes: int = 12,
) -> tuple[float, float]:
    """Find [t_start, t_end] where a region is on-screen around t_hint.

    Walks backward/forward in `step` increments (capped at max_probes each
    direction). Independent of shot cuts — used for continuous promo ads
    where a lower-third enters mid-clip.
    """
    dur = max(0.1, float(duration))
    hint = min(max(0.0, float(t_hint)), dur)
    if not is_present(hint):
        # Hint frame missed the glyph; keep a tight window around hint.
        return (max(0.0, hint - step), min(dur, hint + step))

    t_start = hint
    for i in range(1, max_probes + 1):
        t = hint - i * step
        if t < 0:
            t_start = 0.0
            break
        if is_present(t):
            t_start = t
        else:
            break

    t_end = hint
    for i in range(1, max_probes + 1):
        t = hint + i * step
        if t > dur:
            t_end = dur
            break
        if is_present(t):
            t_end = t
        else:
            break

    # Expand by half-step so enable=between covers fade edges
    pad = step * 0.5
    t0 = max(0.0, t_start - pad)
    t1 = min(dur, t_end + pad)
    if t1 <= t0:
        t1 = min(dur, t0 + max(step, 0.2))
    return (t0, t1)


def crop_similarity(ref_rgb, probe_rgb, *, threshold: float = 0.55) -> bool:
    """True when probe crop still resembles the reference (template match / NCC)."""
    import numpy as np

    ref = np.asarray(ref_rgb)
    probe = np.asarray(probe_rgb)
    if ref.size == 0 or probe.size == 0:
        return False
    if ref.shape[:2] != probe.shape[:2]:
        try:
            from PIL import Image

            probe = np.asarray(
                Image.fromarray(probe).resize((ref.shape[1], ref.shape[0]), Image.Resampling.BILINEAR)
            )
        except Exception:
            return False
    try:
        import cv2  # type: ignore

        ref_g = cv2.cvtColor(ref, cv2.COLOR_RGB2GRAY) if ref.ndim == 3 else ref
        probe_g = cv2.cvtColor(probe, cv2.COLOR_RGB2GRAY) if probe.ndim == 3 else probe
        if ref_g.shape[0] < 4 or ref_g.shape[1] < 4:
            mad = float(np.mean(np.abs(ref_g.astype(np.float32) - probe_g.astype(np.float32))))
            return mad < 40.0
        res = cv2.matchTemplate(probe_g, ref_g, cv2.TM_CCOEFF_NORMED)
        score = float(res.max()) if res.size else 0.0
        return score >= threshold
    except Exception:
        mad = float(np.mean(np.abs(ref.astype(np.float32) - probe.astype(np.float32))))
        return mad < 35.0


def _fallback_full(video_path: Path, duration_hint: float | None = None) -> list[tuple[float, float]]:
    duration = duration_hint
    if duration is None or duration <= 0:
        try:
            import subprocess

            from app.config import get_settings

            settings = get_settings()
            proc = subprocess.run(
                [
                    settings.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            duration = float(proc.stdout.strip()) if proc.returncode == 0 else 10.0
        except Exception:
            duration = 10.0
    return [(0.0, max(duration, 0.1))]
