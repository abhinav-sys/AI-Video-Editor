"""Shot boundary detection via PySceneDetect (with duration fallback)."""

from __future__ import annotations

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
