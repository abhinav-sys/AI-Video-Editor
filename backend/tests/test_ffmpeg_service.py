from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.api.schemas.edits import EditInstructions, TextReplace, WatermarkPosition
from app.services.ffmpeg_service import FFmpegService, POSITION_EXPR
from app.services.text_ocr import (
    OcrBox,
    apply_substring_replace,
    build_render_regions,
    normalize_text,
    texts_match,
)


def test_position_map_complete():
    assert set(POSITION_EXPR.keys()) == set(WatermarkPosition)


def test_normalize_and_fuzzy_dates():
    assert texts_match("15th & 16th August, 2026 | 10 AM – 6 PM", "15 & 16 august")
    assert texts_match("ON 15TH & 16TH OF AUGUST", "15 & 16 august")
    assert normalize_text("15th & 16th") == "15 & 16"


def test_substring_keeps_time_suffix():
    line = "15th & 16th August, 2026 | 10 AM – 6 PM"
    out = apply_substring_replace(line, "15 & 16 august", "15 & 16 july")
    assert "2026" in out
    assert "10 AM" in out or "10 am" in out.lower()
    assert "july" in out.lower()
    assert "august" not in out.lower()


def test_build_filter_with_ocr_regions(tmp_path: Path):
    # Minimal frame so style sampling works
    frame = tmp_path / "frame.png"
    im = Image.new("RGB", (400, 800), (10, 47, 66))
    draw = ImageDraw.Draw(im)
    draw.text((40, 600), "15th & 16th August, 2026 | 10 AM", fill=(255, 255, 255))
    im.save(frame)

    boxes = [
        OcrBox(
            text="15th & 16th August, 2026 | 10 AM",
            x=40,
            y=600,
            w=320,
            h=28,
            confidence=0.9,
        ),
        OcrBox(
            text="ON 15TH & 16TH OF AUGUST",
            x=80,
            y=400,
            w=240,
            h=24,
            confidence=0.85,
        ),
    ]
    pairs = [TextReplace.model_validate({"from": "15 & 16 august", "to": "15 & 16 july"})]
    regions = build_render_regions(frame, pairs, boxes)
    assert len(regions) == 2
    assert all("july" in r.text.lower() for r in regions)
    assert all(r.font_rgb != (0, 0, 0) for r in regions)
    # Surgical spans must not expand to ~78% frame width
    assert all(r.w <= 320 + 20 for r in regions)

    svc = FFmpegService()
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "15 & 16 august", "to": "15 & 16 july"}]}
    )
    extras, fc = svc.build_filter_complex(inst, None, None, text_regions=regions)
    assert extras == []
    assert fc.count("drawtext") == 2
    assert "drawbox=" not in fc  # invisible path: no opaque stamp
    # With healed banner patches, filter should overlay — without patches, drawtext only
    assert "fontcolor=0x" in fc
    assert "15 \\& 16 july" in fc or "july" in fc.lower()
    # Text is positioned inside the span, not frame-wide (w-text_w)/2 alone
    assert "(w-text_w)/2" not in fc or "+(" in fc
    # Baseline / text_y should appear as numeric y= (not only box-centered formula)
    assert any(getattr(r, "text_y", 0) > 0 for r in regions)
    for r in regions:
        assert f"y={r.text_y}" in fc or f"y={r.y}" in fc


def test_build_filter_requires_regions_for_text():
    svc = FFmpegService()
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "July", "to": "August"}]}
    )
    with pytest.raises(ValueError, match="No OCR text regions"):
        svc.build_filter_complex(inst, None, None, text_regions=[])


def test_unmatched_from_errors(tmp_path: Path):
    frame = tmp_path / "frame.png"
    Image.new("RGB", (200, 200), (10, 47, 66)).save(frame)
    boxes = [OcrBox(text="Holiday Inn Parramatta", x=10, y=100, w=180, h=20, confidence=0.9)]
    pairs = [TextReplace.model_validate({"from": "15 & 16 august", "to": "15 & 16 july"})]
    with pytest.raises(ValueError, match="Text not found"):
        build_render_regions(frame, pairs, boxes)


def test_build_filter_with_logo(tmp_path: Path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"fake")
    svc = FFmpegService()
    inst = EditInstructions.model_validate({"replace_logo": "logo.png"})
    extras, fc = svc.build_filter_complex(inst, logo, None)
    assert str(logo) in extras
    assert "overlay" in fc
