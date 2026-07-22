from __future__ import annotations

import json
import re

from pydantic import ValidationError

from app.api.schemas.edits import EditInstructions, ParseResult
from app.core.logging import get_logger

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def extract_json_object(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = _JSON_BLOCK.search(text)
    if not match:
        raise ValueError("No JSON object found in LLM response")
    return match.group(0)


def parse_and_validate(raw: str) -> ParseResult:
    blob = extract_json_object(raw)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON from LLM: {exc}") from exc

    try:
        instructions = EditInstructions.model_validate(data)
    except ValidationError as exc:
        logger.warning("EditInstructions validation failed: %s", exc)
        raise ValueError(f"Invalid edit instructions: {exc}") from exc

    return ParseResult(instructions=instructions, raw=blob)
