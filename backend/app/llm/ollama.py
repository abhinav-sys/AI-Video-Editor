from __future__ import annotations

import httpx

from app.api.schemas.edits import EditInstructions
from app.config import get_settings
from app.core.logging import get_logger
from app.llm.parser import parse_and_validate
from app.llm.prompt import FEW_SHOT_ASSISTANT, FEW_SHOT_USER, SYSTEM_PROMPT, build_user_prompt

logger = get_logger(__name__)


class OllamaProvider:
    name = "ollama"

    def __init__(self) -> None:
        self.settings = get_settings()

    async def parse_prompt(
        self, prompt: str, asset_names: list[str] | None = None
    ) -> EditInstructions:
        payload = {
            "model": self.settings.ollama_model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": FEW_SHOT_USER},
                {"role": "assistant", "content": FEW_SHOT_ASSISTANT},
                {"role": "user", "content": build_user_prompt(prompt, asset_names)},
            ],
            "options": {"temperature": 0.1},
        }
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/chat"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        content = data.get("message", {}).get("content", "")
        if not content:
            raise ValueError("Empty response from Ollama")
        logger.info("Ollama raw response length=%d", len(content))
        result = parse_and_validate(content)
        return result.instructions

    async def health_check(self) -> bool:
        try:
            url = f"{self.settings.ollama_base_url.rstrip('/')}/api/tags"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except Exception:
            return False
