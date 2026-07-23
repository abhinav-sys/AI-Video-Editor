from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from app.api.schemas.edits import TextReplace
from app.services.text_ocr import (
    OcrBox,
    RenderRegion,
    apply_substring_replace,
    assert_no_suspicious_partial,
    build_render_regions,
    is_short_token_from,
    match_replacements_to_boxes,
    merge_render_regions,
    ocr_best_regions_for_replacements,
    texts_match,
)


def test_match_all_occurrences():
    boxes = [
        OcrBox("Godrej India Property Show in Sydney", 20, 1500, 900, 30, 0.9),
        OcrBox("Sydney", 200, 700, 200, 60, 0.95),
        OcrBox("Holiday Inn Parramatta", 40, 1550, 800, 28, 0.88),
    ]
    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]
    matched = match_replacements_to_boxes(boxes, pairs, match_all=True)
    assert len(matched["Sydney"]) == 2


def test_fuzzy_ordinal_dates():
    assert texts_match("ON 15TH & 16TH OF AUGUST", "15 & 16 august")
    assert not texts_match("Holiday Inn Parramatta", "15 & 16 august")


def test_ordered_tokens_with_ocr_noise():
    assert texts_match("15TH 1 16TH OF AUGUST 2026", "15 & 16 august")
    assert texts_match("ON 15 & 16 AUGUST | 10 AM", "15 & 16 august")
    # EasyOCR often reads 15th as I5th
    assert texts_match("I5th & I6th August, 2026", "15 & 16 august")
    assert texts_match("I5th & 16th OF AUGUST", "15 & 16 august")


def test_stitch_line_boxes_merges_date_fragments():
    from app.services.text_ocr import stitch_line_boxes

    boxes = [
        OcrBox("I5th", 40, 800, 60, 28, 0.9),
        OcrBox("&", 105, 802, 20, 24, 0.7),
        OcrBox("I6th", 130, 800, 60, 28, 0.91),
        OcrBox("August", 200, 801, 140, 28, 0.88),
        OcrBox("Sydney", 180, 400, 180, 50, 0.95),
    ]
    merged = stitch_line_boxes(boxes)
    assert any(texts_match(b.text, "15 & 16 august") for b in merged)
    assert any(texts_match(b.text, "Sydney") for b in merged)


def test_substring_month_swap_keeps_rest():
    out = apply_substring_replace(
        "15th & 16th August, 2026 | 10 AM – 6 PM",
        "August",
        "July",
    )
    assert "July" in out
    assert "2026" in out
    assert "10 AM" in out


def test_multi_pair_regions(tmp_path: Path):
    frame = tmp_path / "f.png"
    im = Image.new("RGB", (540, 960), (12, 40, 55))
    d = ImageDraw.Draw(im)
    d.rectangle((40, 700, 500, 860), fill=(10, 47, 66))
    d.text((60, 720), "Show in Sydney", fill=(255, 255, 255))
    d.text((60, 800), "15th & 16th August, 2026 | 10 AM", fill=(240, 240, 240))
    d.text((180, 400), "Sydney", fill=(245, 230, 180))
    im.save(frame)

    boxes = [
        OcrBox("Show in Sydney", 60, 720, 400, 28, 0.9),
        OcrBox("15th & 16th August, 2026 | 10 AM", 60, 800, 420, 28, 0.91),
        OcrBox("Sydney", 180, 400, 180, 50, 0.94),
    ]
    pairs = [
        TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"}),
        TextReplace.model_validate({"from": "15 & 16 august", "to": "15 & 16 july"}),
    ]
    regions = build_render_regions(frame, pairs, boxes)
    texts = [r.text.lower() for r in regions]
    # Surgical: paint only the replacement `to`, not the whole line
    assert any(t == "melbourne" for t in texts)
    assert any("july" in t for t in texts)
    # city-only box should not invent date content
    city = next(r for r in regions if r.from_text == "Sydney" and r.ocr_text == "Sydney")
    assert city.text == "Melbourne"
    # Title span covers only "Sydney", not the full OCR line width
    title = next(r for r in regions if r.ocr_text == "Show in Sydney")
    assert title.text == "Melbourne"
    assert title.w < 400 * 0.55


def test_surgical_span_no_banner_widen(tmp_path: Path):
    frame = tmp_path / "f.png"
    frame_w, frame_h = 1080, 1920
    im = Image.new("RGB", (frame_w, frame_h), (12, 40, 55))
    ImageDraw.Draw(im).rectangle((40, 1500, 1040, 1700), fill=(10, 47, 66))
    im.save(frame)

    boxes = [
        OcrBox("Godrej India Property Show in Sydney", 80, 1520, 900, 28, 0.92),
        OcrBox("15th & 16th August, 2026 | 10 AM – 6 PM", 100, 1620, 850, 26, 0.9),
    ]
    pairs = [
        TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"}),
        TextReplace.model_validate({"from": "15 & 16 august", "to": "15 & 16 july"}),
    ]
    regions = build_render_regions(frame, pairs, boxes)
    for r in regions:
        # Never invent a banner-wide wipe unless OCR box itself was that wide
        if r.ocr_text.startswith("Godrej"):
            assert r.w < frame_w * 0.55
            assert r.text == "Melbourne"
        if "August" in r.ocr_text:
            assert r.w < frame_w * 0.55
            assert "july" in r.text.lower()
            assert "2026" not in r.text  # year stays on the original pixels


def test_find_match_char_range_and_substring():
    from app.services.text_ocr import find_match_char_range

    line = "Godrej India Property Show in Sydney"
    span = find_match_char_range(line, "Sydney")
    assert span is not None
    assert line[span[0] : span[1]].lower() == "sydney"

    date = "15th & 16th August, 2026 | 10 AM – 6 PM"
    rebuilt = apply_substring_replace(date, "15 & 16 august", "15 & 16 july")
    assert "2026" in rebuilt
    assert "july" in rebuilt.lower()


def _region(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    from_text: str = "Sydney",
    ocr_text: str = "Sydney",
    text: str = "Melbourne",
) -> RenderRegion:
    return RenderRegion(
        x=x,
        y=y,
        w=w,
        h=h,
        fill_rgb=(10, 47, 66),
        font_rgb=(255, 255, 255),
        fontsize=24,
        text=text,
        align="center",
        from_text=from_text,
        ocr_text=ocr_text,
    )


def test_merge_union_across_frames():
    """Banner Sydney on frame A + middle Sydney on frame B → both after merge."""
    banner = _region(x=40, y=1500, w=400, h=30, ocr_text="Show in Sydney", text="Show in Melbourne")
    middle = _region(x=200, y=700, w=220, h=70, ocr_text="Sydney", text="Melbourne")
    merged = merge_render_regions([banner, middle], frame_w=1080, frame_h=1920)
    assert len(merged) == 2
    ys = sorted(r.y for r in merged)
    assert ys[0] < 900
    assert ys[1] > 1400


def test_merge_iou_dedupe():
    """Overlapping same from_ on two frames collapses to one region."""
    a = _region(x=200, y=700, w=200, h=60)
    b = _region(x=205, y=705, w=210, h=65)  # high IoU with a
    merged = merge_render_regions([a, b], frame_w=1080, frame_h=1920)
    assert len(merged) == 1
    # Prefer larger box
    assert merged[0].w * merged[0].h == 210 * 65


def test_is_short_token_from():
    assert is_short_token_from("Sydney")
    assert is_short_token_from("the Sydney")
    assert not is_short_token_from("15 & 16 august")


def test_assert_suspicious_partial_fails_on_portrait():
    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]
    only_banner = [_region(x=40, y=1500, w=400, h=30, ocr_text="Show in Sydney")]
    with pytest.raises(ValueError, match="1 place"):
        assert_no_suspicious_partial(only_banner, pairs, frame_w=1080, frame_h=1920)


def test_assert_suspicious_partial_ok_with_two():
    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]
    regions = [
        _region(x=40, y=1500, w=400, h=30, ocr_text="Show in Sydney"),
        _region(x=200, y=700, w=220, h=70, ocr_text="Sydney"),
    ]
    assert_no_suspicious_partial(regions, pairs, frame_w=1080, frame_h=1920)


def test_assert_suspicious_partial_skips_landscape():
    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]
    only_one = [_region(x=40, y=400, w=200, h=40)]
    # Landscape — guard does not apply
    assert_no_suspicious_partial(only_one, pairs, frame_w=1920, frame_h=1080)


def test_ocr_best_unions_frames(tmp_path: Path):
    """Mock OCR: each frame sees a different Sydney; union keeps both."""
    frame_a = tmp_path / "a.png"
    frame_b = tmp_path / "b.png"
    for path, y in ((frame_a, 800), (frame_b, 400)):
        im = Image.new("RGB", (540, 960), (12, 40, 55))
        ImageDraw.Draw(im).rectangle((20, y, 500, y + 40), fill=(10, 47, 66))
        im.save(path)

    boxes_by_frame = {
        frame_a.resolve(): [
            OcrBox("Show in Sydney", 40, 800, 400, 30, 0.9),
        ],
        frame_b.resolve(): [
            OcrBox("Sydney", 180, 400, 200, 60, 0.95),
        ],
    }

    def fake_robust(path: Path, *, reader_factory=None):
        return boxes_by_frame[Path(path).resolve()]

    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]

    with (
        patch("app.services.text_ocr.read_ocr_boxes_robust", side_effect=fake_robust),
        patch("app.services.text_ocr.read_ocr_boxes", side_effect=fake_robust),
    ):
        regions, best = ocr_best_regions_for_replacements(
            [frame_a, frame_b],
            pairs,
            use_boost=False,
            enforce_multi_short=False,
        )

    assert len(regions) == 2
    assert best is not None
    assert all("melbourne" in r.text.lower() for r in regions)
    ys = sorted(r.y for r in regions)
    assert ys[0] < 500
    assert ys[1] > 700


def test_ocr_best_proceeds_partial_short_after_retry(tmp_path: Path):
    """After boost, a single portrait short-token hit warns but still returns regions."""
    frame = tmp_path / "only.png"
    im = Image.new("RGB", (540, 960), (12, 40, 55))
    ImageDraw.Draw(im).rectangle((20, 800, 500, 840), fill=(10, 47, 66))
    im.save(frame)

    only_banner = [OcrBox("Show in Sydney", 40, 800, 400, 30, 0.9)]

    def fake_robust(path: Path, *, reader_factory=None):
        return only_banner

    pairs = [TextReplace.model_validate({"from": "Sydney", "to": "Melbourne"})]

    with (
        patch("app.services.text_ocr.read_ocr_boxes_robust", side_effect=fake_robust),
        patch("app.services.text_ocr.read_ocr_boxes", side_effect=fake_robust),
    ):
        regions, best = ocr_best_regions_for_replacements(
            [frame],
            pairs,
            use_boost=False,
            enforce_multi_short=True,
        )

    assert best is not None
    assert len(regions) == 1
    assert "melbourne" in regions[0].text.lower()
