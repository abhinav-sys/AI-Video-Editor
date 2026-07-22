from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas.edits import EditInstructions, WatermarkPosition


def test_valid_instructions():
    data = {
        "replace_text": [{"from": "July", "to": "August"}],
        "replace_logo": "logo.png",
        "watermark": "bottom-right",
    }
    inst = EditInstructions.model_validate(data)
    assert inst.replace_text[0].from_ == "July"
    assert inst.replace_logo == "logo.png"
    assert inst.watermark == WatermarkPosition.bottom_right


def test_reject_unknown_keys():
    with pytest.raises(ValidationError):
        EditInstructions.model_validate({"replace_text": [], "foo": 1, "watermark": "center"})


def test_reject_noop():
    with pytest.raises(ValidationError):
        EditInstructions.model_validate({"replace_text": []})


def test_reject_path_traversal_logo():
    with pytest.raises(ValidationError):
        EditInstructions.model_validate({"replace_logo": "../etc/passwd"})
