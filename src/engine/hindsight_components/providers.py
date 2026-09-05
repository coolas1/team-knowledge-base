"""Hindsight provider adapter over the project's existing model settings."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Protocol

import httpx

from config.settings import settings
from src.engine.components.embedder import embedder

OLLAMA_MAX_OUTPUT_TOKENS = 4096


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
    """Use the configured Ollama/OpenAI-compatible LLM and shared Embedder."""

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
        provider = settings.llm_provider
        if provider == "todo":
            raise RuntimeError("Hindsight LLM is disabled (LLM_PROVIDER=todo)")
        if provider == "ollama":
            return await self._ollama(
                system, user, json_mode=json_mode, timeout=timeout
            )
        if provider in {"openai", "custom"}:
            return await self._openai(
                system, user, json_mode=json_mode, timeout=timeout
            )
        raise ValueError(f"unsupported LLM provider: {provider}")

    @staticmethod
    async def _ollama(
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
    ) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm_model or "qwen3:14b",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0,
                # Qwen can keep emitting whitespace/repeated JSON after a valid
                # object. Bound generation so one retain call cannot occupy the
                # local GPU until the HTTP timeout.
                "num_predict": OLLAMA_MAX_OUTPUT_TOKENS,
            },
        }
        if json_mode:
            payload["format"] = "json"
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.ollama_base_url.rstrip('/')}/api/chat", json=payload
            )
            response.raise_for_status()
            return str(response.json()["message"]["content"])

    @staticmethod
    async def _openai(
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
    ) -> str:
        payload: dict[str, Any] = {
            "model": settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
