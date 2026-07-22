from __future__ import annotations

from pathlib import Path

from app.api.schemas.edits import EditInstructions, WatermarkPosition
from app.services.ffmpeg_service import FFmpegService, POSITION_EXPR


def test_position_map_complete():
    assert set(POSITION_EXPR.keys()) == set(WatermarkPosition)


def test_build_filter_with_text_only():
    svc = FFmpegService()
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "July", "to": "August"}]}
    )
    extras, fc = svc.build_filter_complex(inst, None, None)
    assert extras == []
    assert "drawtext" in fc
    assert "drawbox" in fc
    assert "August" in fc


def test_build_filter_with_located_region():
    from app.services.text_locate import TextRegion

    svc = FFmpegService()
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "15 & 16 august", "to": "26 & 27 september"}]}
    )
    region = TextRegion(x=100, y=1600, w=880, h=36, fill_rgb=(10, 47, 66), fontsize=28)
    extras, fc = svc.build_filter_complex(inst, None, None, text_regions=[region])
    assert extras == []
    assert "x=100" in fc and "y=1600" in fc
    assert "0x0A2F42" in fc
    assert "26 \\& 27 september" in fc



def test_build_filter_with_logo(tmp_path: Path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"fake")
    svc = FFmpegService()
    inst = EditInstructions.model_validate({"replace_logo": "logo.png"})
    extras, fc = svc.build_filter_complex(inst, logo, None)
    assert str(logo) in extras
    assert "overlay" in fc
