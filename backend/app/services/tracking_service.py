"""Lightweight IoU multi-object tracker for entity continuity across shots."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Detection:
    frame_idx: int
    t: float
    bbox: tuple[int, int, int, int]  # x,y,w,h
    label: str = ""
    score: float = 1.0


@dataclass
class Track:
    track_id: int
    label: str
    detections: list[Detection] = field(default_factory=list)

    @property
    def t_start(self) -> float:
        return min(d.t for d in self.detections) if self.detections else 0.0

    @property
    def t_end(self) -> float:
        return max(d.t for d in self.detections) if self.detections else 0.0

    @property
    def last_bbox(self) -> tuple[int, int, int, int] | None:
        return self.detections[-1].bbox if self.detections else None


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def track_detections(
    detections: list[Detection],
    *,
    iou_threshold: float = 0.3,
) -> list[Track]:
    """Greedy IoU association across increasing frame_idx (ByteTrack-lite)."""
    by_frame: dict[int, list[Detection]] = {}
    for d in detections:
        by_frame.setdefault(d.frame_idx, []).append(d)

    tracks: list[Track] = []
    next_id = 1
    for frame_idx in sorted(by_frame):
        dets = by_frame[frame_idx]
        unmatched = set(range(len(dets)))
        # Match to existing tracks by best IoU
        for tr in tracks:
            if tr.last_bbox is None:
                continue
            best_j, best_iou = -1, iou_threshold
            for j in list(unmatched):
                score = _iou(tr.last_bbox, dets[j].bbox)
                # Prefer same label when present
                if tr.label and dets[j].label and tr.label != dets[j].label:
                    score *= 0.5
                if score > best_iou:
                    best_iou = score
                    best_j = j
            if best_j >= 0:
                tr.detections.append(dets[best_j])
                unmatched.discard(best_j)
        for j in unmatched:
            d = dets[j]
            tracks.append(Track(track_id=next_id, label=d.label, detections=[d]))
            next_id += 1
    return tracks


def guess_opacity_curve(track: Track) -> str | None:
    """Infer simple fade from bbox area deltas (heuristic)."""
    if len(track.detections) < 2:
        return None
    areas = [d.bbox[2] * d.bbox[3] for d in track.detections]
    if areas[0] < areas[-1] * 0.7:
        return "fade_in:0.3"
    if areas[-1] < areas[0] * 0.7:
        return "fade_out:0.3"
    return None
