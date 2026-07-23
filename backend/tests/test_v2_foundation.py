"""Unit tests for V2 helpers: enable_between, shots, template, tracking."""

from __future__ import annotations

from app.api.schemas.template import EditableTemplate, EntityType, TrackSegment, TemplateEntity, VideoMeta, EntityStyle
from app.services.ffmpeg_kit import enable_between
from app.services.scene_detect import refine_presence_window, shot_sample_times
from app.services.timeline_service import regions_to_template, template_to_regions
from app.services.tracking_service import Detection, track_detections
from app.services.text_ocr import RenderRegion
from app.services.upload_validation import sniff_is_image, sniff_is_video


def test_enable_between_formats():
    assert enable_between(None, None) == ""
    expr = enable_between(2.1, 9.8)
    assert "between(t,2.100,9.800)" in expr
    assert expr.startswith(":enable=")


def test_shot_sample_times_caps():
    shots = [(float(i), float(i + 1)) for i in range(20)]
    samples = shot_sample_times(shots, max_shots=5)
    assert len(samples) == 5
    for sample_t, t0, t1 in samples:
        assert t0 <= sample_t <= t1


def test_refine_presence_window_finds_bounds():
    # Present only between 2.0 and 8.0
    def is_present(t: float) -> bool:
        return 2.0 <= t <= 8.0

    t0, t1 = refine_presence_window(5.0, 12.0, is_present, step=0.5, max_probes=12)
    assert t0 <= 2.5
    assert t1 >= 7.5
    assert t1 - t0 < 12.0  # not full duration


def test_refine_presence_window_missed_hint():
    def never(_: float) -> bool:
        return False

    t0, t1 = refine_presence_window(4.0, 10.0, never, step=0.5)
    assert 3.0 <= t0 <= 4.0
    assert 4.0 <= t1 <= 5.0


def test_regions_to_template_roundtrip():
    regions = [
        RenderRegion(
            x=10,
            y=20,
            w=100,
            h=40,
            fill_rgb=(10, 20, 30),
            font_rgb=(255, 255, 255),
            fontsize=32,
            text="Melbourne",
            align="left",
            from_text="Sydney",
            ocr_text="Sydney",
            t_start=1.0,
            t_end=5.0,
            entity_id="e1",
        )
    ]
    tmpl = regions_to_template(regions, duration=10.0, width=1080, height=1920)
    assert isinstance(tmpl, EditableTemplate)
    assert len(tmpl.entities) == 1
    assert tmpl.entities[0].track[0].t_start == 1.0
    back = template_to_regions(tmpl)
    assert len(back) == 1
    assert back[0].text == "Melbourne"
    assert back[0].t_start == 1.0


def test_track_detections_iou():
    dets = [
        Detection(0, 0.0, (10, 10, 50, 20), label="a"),
        Detection(1, 1.0, (12, 11, 50, 20), label="a"),
        Detection(1, 1.0, (200, 200, 40, 40), label="b"),
    ]
    tracks = track_detections(dets, iou_threshold=0.3)
    assert len(tracks) == 2
    lengths = sorted(len(t.detections) for t in tracks)
    assert lengths == [1, 2]


def test_sniff_video_mp4_ftyp():
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00"
    assert sniff_is_video(header)
    assert sniff_is_image(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    assert not sniff_is_video(b"MZ\x90\x00\x03")


def test_template_entity_schema():
    t = EditableTemplate(
        video=VideoMeta(duration=3.0, width=100, height=200),
        entities=[
            TemplateEntity(
                id="x",
                type=EntityType.text,
                text="hi",
                track=[TrackSegment(t_start=0, t_end=1, bbox=[0, 0, 10, 10])],
                style=EntityStyle(size=12, color="#FFFFFF"),
            )
        ],
    )
    assert t.version == 1
    raw = t.model_dump_json()
    assert "entities" in raw
