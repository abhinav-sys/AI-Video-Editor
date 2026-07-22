from __future__ import annotations

import pytest

from app.llm.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_parse_replace_to():
    provider = MockProvider()
    inst = await provider.parse_prompt("Replace 15 & 16 august to 26 & 27 september")
    assert len(inst.replace_text) == 1
    assert inst.replace_text[0].from_.lower() == "15 & 16 august"
    assert inst.replace_text[0].to.lower() == "26 & 27 september"


@pytest.mark.asyncio
async def test_parse_replace_with():
    provider = MockProvider()
    inst = await provider.parse_prompt("Replace July with August")
    assert inst.replace_text[0].from_ == "July"
    assert inst.replace_text[0].to == "August"
