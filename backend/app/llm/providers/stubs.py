"""Future cloud / video LLM providers — stubs for Gemini, OpenAI, Runway."""

from __future__ import annotations

from app.api.schemas.edits import EditInstructions


class GeminiProvider:
    name = "gemini"

    async def parse_prompt(
        self, prompt: str, asset_names: list[str] | None = None
    ) -> EditInstructions:
        raise NotImplementedError("Gemini provider not implemented yet")

    async def health_check(self) -> bool:
        return False


class OpenAIProvider:
    name = "openai"

    async def parse_prompt(
        self, prompt: str, asset_names: list[str] | None = None
    ) -> EditInstructions:
        raise NotImplementedError("OpenAI provider not implemented yet")

    async def health_check(self) -> bool:
        return False


class RunwayProvider:
    name = "runway"

    async def parse_prompt(
        self, prompt: str, asset_names: list[str] | None = None
    ) -> EditInstructions:
        raise NotImplementedError("Runway provider not implemented yet")

    async def health_check(self) -> bool:
        return False
