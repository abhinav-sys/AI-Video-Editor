from __future__ import annotations

"""Deterministic offline provider for local demos without Ollama."""

import json
import re

from app.api.schemas.edits import EditInstructions
from app.llm.parser import parse_and_validate

# Stop before the next clause so multi-pair prompts parse cleanly.
_REPLACE_RE = re.compile(
    r"(?:replace|change)\s+['\"]?(.+?)['\"]?\s+(?:with|to)\s+['\"]?(.+?)['\"]?"
    r"(?=,"
    r"|\."
    r"|$"
    r"|\s+and\s+(?:replace|change)\b"
    r"|\s+replace\b"
    r"|\s+change\b"
    r"|\s+add\b)",
    flags=re.IGNORECASE,
)


class MockProvider:
    name = "mock"

    async def parse_prompt(
        self, prompt: str, asset_names: list[str] | None = None
    ) -> EditInstructions:
        assets = asset_names or []
        logo = next((a for a in assets if "logo" in a.lower()), assets[0] if assets else None)
        data: dict = {"replace_text": []}

        for m in _REPLACE_RE.finditer(prompt):
            left, right = m.group(1).strip(" '\"."), m.group(2).strip(" '\".")
            if not left or not right:
                continue
            if "logo" in left.lower():
                data["replace_logo"] = (
                    right if right.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else logo
                )
            else:
                data["replace_text"].append({"from": left, "to": right})

        if re.search(r"\bwatermark\b", prompt, re.IGNORECASE):
            pos = "bottom-right"
            for candidate in (
                "top-left",
                "top-right",
                "bottom-left",
                "bottom-right",
                "center",
            ):
                if candidate in prompt.lower():
                    pos = candidate
                    break
            data["watermark"] = pos
            if logo:
                data["watermark_image"] = logo

        if "replace_logo" not in data and logo and re.search(r"\blogo\b", prompt, re.IGNORECASE):
            data["replace_logo"] = logo

        if not data["replace_text"] and "replace_logo" not in data and "watermark" not in data:
            raise ValueError(
                "Could not parse any text replacements, logo, or watermark from the prompt. "
                "Try: Replace <old text> to <new text>"
            )

        return parse_and_validate(json.dumps(data)).instructions

    async def health_check(self) -> bool:
        return True
