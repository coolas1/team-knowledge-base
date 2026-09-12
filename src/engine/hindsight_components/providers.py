"""Hindsight provider adapter over the project's existing model settings."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Protocol

import httpx

from config.settings import settings
from src.engine.components.embedder import embedder
from src.engine.components.llm_options import (
    memory_options,
    memory_identity,
    log_completion,
)


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

    @property
    def extraction_identity(self) -> str:
        return memory_identity(settings.llm)

    async def embed(
        self, texts: list[str], *, timeout: float | None = None
    ) -> list[list[float]]:
        if timeout is None:
            return await self._embedding_provider.embed_batch(texts)
        async with asyncio.timeout(timeout):
            return await self._embedding_provider.embed_batch(texts)

    async def json(
        self,
        system: str,
        user: str,
        *,
        timeout: float = 600,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return parse_json_object(
            await self._complete(
                system, user, json_mode=True, timeout=timeout, max_tokens=max_tokens
            )
        )

    async def json_with_usage(
        self,
        system: str,
        user: str,
        *,
        timeout: float = 600,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return structured output with provider-owned usage telemetry."""
        if not settings.llm.enabled:
            raise RuntimeError("Hindsight LLM is disabled (LLM_BASE_URL is empty)")
        response = await self._openai_response(
            system, user, json_mode=True, timeout=timeout, max_tokens=max_tokens
        )
        content = str(response["choices"][0]["message"]["content"])
        usage = response.get("usage")
        return parse_json_object(content), usage if isinstance(usage, dict) else {}

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
        max_tokens: int | None = None,
    ) -> str:
        if not settings.llm.enabled:
            raise RuntimeError("Hindsight LLM is disabled (LLM_BASE_URL is empty)")
        return await self._openai(
            system,
            user,
            json_mode=json_mode,
            timeout=timeout,
            max_tokens=max_tokens,
        )

    @staticmethod
    async def _openai(
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
        max_tokens: int | None = None,
    ) -> str:
        response = await ProjectHindsightProviders._openai_response(
            system,
            user,
            json_mode=json_mode,
            timeout=timeout,
            max_tokens=max_tokens,
        )
        return str(response["choices"][0]["message"]["content"])

    @staticmethod
    async def _openai_response(
        system: str,
        user: str,
        *,
        json_mode: bool,
        timeout: float,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
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
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(
            memory_options(
                payload["model"],
                settings.llm.base_url,
                settings.llm.memory_thinking,
                bounded=json_mode and max_tokens is not None,
            )
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.llm.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("LLM response is not a JSON object")
            log_completion(logging.getLogger(__name__), payload, value)
            return value
