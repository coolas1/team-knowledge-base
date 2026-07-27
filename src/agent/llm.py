"""ConfiguredLlmClient: an LlmClient backed by the OpenAI-compatible LLM
configured in config/engine/graphrag/model_config.yaml. Used by the webapp BFF
to synthesize answers/summaries when invoking agent skills in-process.

(The codex harness, when run as its own process, supplies its own LLM or lets
codex synthesize - skills tolerate ctx.llm=None.)
"""
from __future__ import annotations

from pathlib import Path

import httpx
import yaml

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


def build_llm(model_config_path: Path) -> ConfiguredLlmClient | None:
    """Build a ConfiguredLlmClient from model_config.yaml; None if provider is 'todo'."""
    if not model_config_path.exists():
        return None
    data = yaml.safe_load(model_config_path.read_text(encoding="utf-8")) or {}
    llm = data.get("llm", {})
    if llm.get("provider", "todo") == "todo":
        return None
    base_url = llm.get("base_url", "https://api.openai.com/v1")
    model = llm.get("model", "gpt-4o-mini")
    api_key = llm.get("api_key", "") or settings.llm_api_key
    return ConfiguredLlmClient(base_url, model, api_key)
