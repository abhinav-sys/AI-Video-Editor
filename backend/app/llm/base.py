from __future__ import annotations

from typing import Protocol

from app.api.schemas.edits import EditInstructions


class LLMProvider(Protocol):
    """Protocol for prompt → structured edit JSON providers."""

    name: str

    async def parse_prompt(self, prompt: str, asset_names: list[str] | None = None) -> EditInstructions:
        ...

    async def health_check(self) -> bool:
        ...
