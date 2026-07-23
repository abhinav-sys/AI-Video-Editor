from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.api.schemas.edits import EditInstructions, TextReplace, WatermarkPosition
from app.services.ffmpeg_service import FFmpegService, POSITION_EXPR
from app.services.text_ocr import (
    OcrBox,
    RenderRegion,
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
    extras, fc, final_label = svc.build_filter_complex(inst, None, None, text_regions=regions)
    assert extras == []
    assert final_label.startswith("[") and final_label.endswith("]")
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


def test_build_filter_heal_patch_uses_loop(tmp_path: Path):
    from app.services.text_heal import HealedPatch

    patch = tmp_path / "heal.png"
    Image.new("RGBA", (20, 10), (10, 47, 66, 200)).save(patch)
    region = RenderRegion(
        x=10,
        y=20,
        w=100,
        h=30,
        fill_rgb=(10, 47, 66),
        font_rgb=(255, 255, 255),
        fontsize=24,
        text="Hi",
        align="left",
        from_text="Old",
        ocr_text="Old",
    )
    svc = FFmpegService()
    inst = EditInstructions.model_validate({"replace_text": [{"from": "Old", "to": "Hi"}]})
    hp = HealedPatch(x=8, y=18, path=patch, w=20, h=10, mode="banner", from_text="Old", text="Hi")
    extras, fc, label = svc.build_filter_complex(
        inst, None, None, text_regions=[region], healed_patches=[hp]
    )
    assert extras[:3] == ["-loop", "1", "-i"]
    assert "overlay=" in fc
    assert label == "[t0]"


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
    extras, fc, label = svc.build_filter_complex(inst, logo, None)
    assert str(logo) in extras
    assert "overlay" in fc
    assert label == "[logo]"


@pytest.mark.asyncio
async def test_maybe_trim_off_returns_input(tmp_path: Path):
    svc = FFmpegService()
    svc.settings = svc.settings.model_copy(update={"test_clip_seconds": 0.0})
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 128)
    out = await svc._maybe_trim_test_clip(src, tmp_path)
    assert out == src


@pytest.mark.asyncio
async def test_maybe_trim_invokes_ffmpeg_when_enabled(tmp_path: Path):
    from unittest.mock import AsyncMock, patch

    svc = FFmpegService()
    svc.settings = svc.settings.model_copy(update={"test_clip_seconds": 6.0})
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 128)
    calls: list[list[str]] = []

    class _Proc:
        returncode = 0

        async def communicate(self):
            (tmp_path / "test_clip.mp4").write_bytes(b"trimmed-clip-content-xx" * 8)
            return b"", b""

    async def _fake_exec(*cmd, **_kwargs):
        calls.append([str(c) for c in cmd])
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_exec):
        out = await svc._maybe_trim_test_clip(src, tmp_path)

    assert out == tmp_path / "test_clip.mp4"
    assert calls
    assert "-t" in calls[0]
    assert "6.0" in calls[0] or "6" in calls[0]


@pytest.mark.asyncio
async def test_render_trim_gate_when_test_clip_seconds(tmp_path: Path):
    from unittest.mock import AsyncMock, patch

    svc = FFmpegService()
    svc.settings = svc.settings.model_copy(
        update={"test_clip_seconds": 6.0, "template_only": True}
    )
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 128)
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    region = RenderRegion(
        x=10,
        y=20,
        w=80,
        h=24,
        fill_rgb=(10, 47, 66),
        font_rgb=(255, 255, 255),
        fontsize=20,
        text="Melbourne",
        align="left",
        from_text="Sydney",
        ocr_text="Sydney",
        t_start=0.0,
        t_end=5.0,
    )
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "Sydney", "to": "Melbourne"}]}
    )
    trim_calls: list[Path] = []

    async def _trim(path: Path, work_dir: Path) -> Path:
        trim_calls.append(path)
        return path

    async def _extract(_input: Path, dest: Path, at_seconds: float = 1.0) -> bool:
        Image.new("RGB", (200, 200), (10, 47, 66)).save(dest)
        return True

    with (
        patch.object(svc, "_maybe_trim_test_clip", side_effect=_trim),
        patch.object(svc, "probe_duration", new=AsyncMock(return_value=6.0)),
        patch.object(svc, "probe_video_size", new=AsyncMock(return_value=(200, 200))),
        patch.object(svc, "_extract_frame", side_effect=_extract),
        patch(
            "app.services.ffmpeg_service.write_heal_artifacts",
            return_value=([], None),
        ),
        patch(
            "app.services.ffmpeg_service.detect_logos_and_graphics",
            return_value=[],
        ),
        patch(
            "app.services.ffmpeg_service.paint_invisible_preview",
            side_effect=lambda frame, regions, dest, patches=None: (
                Image.new("RGB", (200, 200), (20, 20, 20)).save(dest) or dest
            ),
        ),
        patch("app.services.ffmpeg_service.inpaint_video_under_mask") as inpaint,
    ):
        result = await svc.render(
            input_path=src,
            output_path=tmp_path / "out.mp4",
            instructions=inst,
            upload_id="upload-1",
            preview_dir=preview_dir,
            text_regions=[region],
        )

    assert trim_calls == [src]
    inpaint.assert_not_called()
    assert result.occurrences == 1
    assert result.template_json is not None
    assert (preview_dir / "template.json").is_file()
    assert not (tmp_path / "out.mp4").is_file()


@pytest.mark.asyncio
async def test_template_only_skips_inpaint_and_encode(tmp_path: Path):
    from unittest.mock import AsyncMock, patch

    svc = FFmpegService()
    svc.settings = svc.settings.model_copy(
        update={"test_clip_seconds": 0.0, "template_only": True}
    )
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x" * 128)
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    region = RenderRegion(
        x=10,
        y=20,
        w=80,
        h=24,
        fill_rgb=(10, 47, 66),
        font_rgb=(255, 255, 255),
        fontsize=20,
        text="july",
        align="left",
        from_text="august",
        ocr_text="august",
        t_start=0.0,
        t_end=4.0,
    )
    inst = EditInstructions.model_validate(
        {"replace_text": [{"from": "august", "to": "july"}]}
    )

    async def _extract(_input: Path, dest: Path, at_seconds: float = 1.0) -> bool:
        Image.new("RGB", (200, 200), (10, 47, 66)).save(dest)
        return True

    encode_called = {"n": 0}

    async def _no_encode(*_a, **_k):
        encode_called["n"] += 1
        raise AssertionError("final encode must not run in template_only mode")

    with (
        patch.object(svc, "probe_duration", new=AsyncMock(return_value=10.0)),
        patch.object(svc, "probe_video_size", new=AsyncMock(return_value=(200, 200))),
        patch.object(svc, "_extract_frame", side_effect=_extract),
        patch.object(svc, "_mark_moving_regions", new=AsyncMock(side_effect=lambda _i, regs, _d: regs)),
        patch(
            "app.services.ffmpeg_service.write_heal_artifacts",
            return_value=([], None),
        ),
        patch(
            "app.services.ffmpeg_service.detect_logos_and_graphics",
            return_value=[],
        ),
        patch(
            "app.services.ffmpeg_service.paint_invisible_preview",
            side_effect=lambda frame, regions, dest, patches=None: (
                Image.new("RGB", (200, 200), (30, 30, 30)).save(dest) or dest
            ),
        ),
        patch("app.services.ffmpeg_service.inpaint_video_under_mask") as inpaint,
        patch("asyncio.create_subprocess_exec", side_effect=_no_encode),
    ):
        result = await svc.render(
            input_path=src,
            output_path=tmp_path / "out.mp4",
            instructions=inst,
            upload_id="upload-1",
            preview_dir=preview_dir,
            text_regions=[region],
        )

    assert encode_called["n"] == 0
    inpaint.assert_not_called()
    assert result.template_json is not None
    assert "july" in result.template_json.lower()
    assert result.preview_after is not None
    assert result.preview_after.is_file()
