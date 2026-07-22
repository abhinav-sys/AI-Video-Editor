from __future__ import annotations

import pytest

from app.llm.parser import extract_json_object, parse_and_validate


def test_extract_from_markdown_fence():
    raw = '```json\n{"replace_text":[{"from":"A","to":"B"}]}\n```'
    blob = extract_json_object(raw)
    assert "replace_text" in blob


def test_parse_and_validate_happy():
    raw = '{"replace_text":[{"from":"July","to":"August"}],"replace_logo":"logo.png","watermark":"bottom-right"}'
    result = parse_and_validate(raw)
    assert result.instructions.replace_logo == "logo.png"


def test_parse_rejects_invalid():
    with pytest.raises(ValueError):
        parse_and_validate("not json at all")
