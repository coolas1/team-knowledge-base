"""Hindsight provider adapter over the project's existing model settings."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

import httpx

from config.settings import settings
from src.engine.components.embedder import embedder


class EmbeddingProvider(Protocol):
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def parse_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match is None:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


class ProjectHindsightProviders:
    """Use the configured OpenAI-compatible LLM and shared Embedder."""

    def __init__(self, embedding_provider: EmbeddingProvider = embedder) -> None:
        self._embedding_provider = embedding_provider

    async def embed(
        self, texts: list[str], *, timeout: float | None = None
    ) -> list[list[float]]:
        if timeout is None:
            return await self._embedding_provider.embed_batch(texts)
        async with asyncio.timeout(timeout):
            return await self._embedding_provider.embed_batch(texts)

    async def json(
        self, system: str, user: str, *, timeout: float = 600
    ) -> dict[str, Any]:
        return parse_json_object(
            await self._complete(system, user, json_mode=True, timeout=timeout)
        )

    async def text(self, system: str, user: str, *, timeout: float = 600) -> str:
        return (
            await self._complete(system, user, json_mode=False, timeout=timeout)
        ).strip()

    async def _complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
    ) -> str:
        if not settings.llm.enabled:
            raise RuntimeError("Hindsight LLM is disabled (LLM_BASE_URL is empty)")
        return await self._openai(system, user, json_mode=json_mode, timeout=timeout)

    @staticmethod
    async def _openai(
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
    ) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm.require_model(),
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            # Some compatible endpoints reject JSON mode unless a message
            # explicitly requests JSON, even when the prompt includes a schema.
            payload["messages"][0]["content"] += "\nReturn a valid JSON object."
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.llm.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
