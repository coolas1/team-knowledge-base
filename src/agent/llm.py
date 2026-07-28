"""ConfiguredLlmClient: an LlmClient backed by the chat LLM configured via the
LLM_* env vars in .env (provider/model/base_url/api_key). Used by the webapp BFF
to synthesize answers/summaries when invoking agent skills in-process.

(The codex harness, when run as its own process, supplies its own LLM or lets
codex synthesize - skills tolerate ctx.llm=None.)
"""
from __future__ import annotations

import httpx

from config.settings import settings
from src.agent.interface import LlmClient


class ConfiguredLlmClient:
    def __init__(self, base_url: str, model: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    async def complete(self, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def build_llm() -> ConfiguredLlmClient | None:
    """Build a ConfiguredLlmClient from .env (LLM_*); None if provider is 'todo'."""
    if settings.llm_provider == "todo":
        return None
    base_url = settings.llm_base_url or "https://api.openai.com/v1"
    model = settings.llm_model or "gpt-4o-mini"
    api_key = settings.llm_api_key
    return ConfiguredLlmClient(base_url, model, api_key)
