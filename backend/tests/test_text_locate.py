from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.text_locate import (
    list_font_candidates,
    match_font_to_crop,
    sample_style_from_box,
)


def test_gold_glyph_color_prefers_warm_not_white(tmp_path: Path):
    """Top-percentile sampling keeps warm gold, not washed-out white median."""
    frame = tmp_path / "gold.png"
    im = Image.new("RGB", (200, 80), (10, 47, 66))
    d = ImageDraw.Draw(im)
    # Mix of white glow and gold core
    d.rectangle((20, 20, 180, 60), fill=(10, 47, 66))
    for x in range(40, 160):
        for y in range(28, 52):
            # Gold body
            im.putpixel((x, y), (240, 200, 90))
    # A few bright white edge pixels that would pull a plain median up
    for x in range(40, 160, 3):
        im.putpixel((x, 28), (255, 255, 255))
    im.save(frame)

    style = sample_style_from_box(frame, 30, 20, 140, 40)
    r, g, b = style.font_rgb
    assert r > 200 and g > 150
    assert r + g > b + 80  # warm, not neutral white
    assert style.baseline_y > 20


def test_fontsize_tracks_glyph_height(tmp_path: Path):
    frame_small = tmp_path / "small.png"
    frame_tall = tmp_path / "tall.png"

    im_s = Image.new("RGB", (120, 40), (10, 47, 66))
    ImageDraw.Draw(im_s).rectangle((10, 12, 110, 28), fill=(250, 250, 250))
    im_s.save(frame_small)

    im_t = Image.new("RGB", (200, 100), (10, 47, 66))
    ImageDraw.Draw(im_t).rectangle((20, 15, 180, 85), fill=(250, 250, 250))
    im_t.save(frame_tall)

    small = sample_style_from_box(frame_small, 5, 5, 110, 30)
    tall = sample_style_from_box(frame_tall, 10, 10, 180, 80)
    assert tall.fontsize > small.fontsize
    assert tall.fontsize >= 60


def test_match_font_returns_existing_file(tmp_path: Path):
    cands = list_font_candidates()
    if not cands:
        return  # skip on machines with no fonts
    frame = tmp_path / "f.png"
    font_path = cands[0][0]
    font = ImageFont.truetype(font_path, 36)
    im = Image.new("RGB", (220, 60), (10, 47, 66))
    ImageDraw.Draw(im).text((20, 10), "Sydney", font=font, fill=(245, 230, 180))
    im.save(frame)

    path, bold = match_font_to_crop(
        frame, 10, 5, 200, 50, "Sydney", fontsize=36, font_rgb=(245, 230, 180)
    )
    assert path is not None
    assert Path(path).is_file()
