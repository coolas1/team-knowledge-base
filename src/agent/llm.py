"""ConfiguredLlmClient: an LlmClient backed by the chat LLM configured via the
LLM_* env vars in .env (base_url/model/api_key). Used by the webapp BFF
to synthesize answers/summaries when invoking plugin skills in-process.

(Skills tolerate ctx.llm=None and fall back to returning raw context/overview.)
"""
from __future__ import annotations

import httpx

from config.settings import settings


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
    """Build a ConfiguredLlmClient from .env (LLM_*); None when disabled.

    Disabled = empty LLM_BASE_URL. Enabled with an empty LLM_MODEL raises
    (a silent gpt-4o-mini fallback would target the wrong deployment).
    """
    if not settings.llm.enabled:
        return None
    model = settings.llm.require_model()
    return ConfiguredLlmClient(
        settings.llm.base_url, model, settings.llm.api_key
    )
