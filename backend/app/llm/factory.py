from __future__ import annotations

from app.config import get_settings
from app.llm.base import LLMProvider
from app.llm.ollama import OllamaProvider
from app.llm.providers import GeminiProvider, MockProvider, OpenAIProvider, RunwayProvider


def get_llm_provider() -> LLMProvider:
    settings = get_settings()
    name = settings.llm_provider.lower().strip()
    mapping: dict[str, type] = {
        "ollama": OllamaProvider,
        "mock": MockProvider,
        "gemini": GeminiProvider,
        "openai": OpenAIProvider,
        "runway": RunwayProvider,
    }
    cls = mapping.get(name, OllamaProvider)
    return cls()
