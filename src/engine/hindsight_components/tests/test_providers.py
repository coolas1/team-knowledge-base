from __future__ import annotations

import pytest

from config.settings import settings
from src.engine.hindsight_components import providers as provider_module
from src.engine.hindsight_components.providers import (
    ProjectHindsightProviders,
    parse_json_object,
)


class FakeEmbedder:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeClient:
    response: dict = {}
    request: tuple[str, dict, dict | None] | None = None

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict | None = None):
        type(self).request = (url, json, headers)
        return FakeResponse(type(self).response)


def test_parse_json_object_accepts_fences_and_surrounding_text() -> None:
    assert parse_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert parse_json_object('result: {"ok": true} done') == {"ok": True}


async def test_provider_reuses_existing_embedder() -> None:
    provider = ProjectHindsightProviders(FakeEmbedder())

    assert await provider.embed(["one", "two"]) == [[1.0, 0.0], [1.0, 0.0]]


async def test_disabled_llm_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.llm, "base_url", "")

    class _NoNetwork:
        def __init__(self, *a, **kw):
            raise AssertionError("network call attempted while LLM disabled")

    monkeypatch.setattr(provider_module.httpx, "AsyncClient", _NoNetwork)
    provider = ProjectHindsightProviders(FakeEmbedder())

    with pytest.raises(RuntimeError, match="LLM_BASE_URL is empty"):
        await provider.json("system", "user")


async def test_llm_uses_openai_compatible_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "remote-model")
    monkeypatch.setattr(settings.llm, "api_key", "secret")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    FakeClient.response = {"choices": [{"message": {"content": "answer"}}]}
    provider = ProjectHindsightProviders(FakeEmbedder())

    assert await provider.text("system", "user") == "answer"
    assert FakeClient.request is not None
    url, payload, headers = FakeClient.request
    assert url == "https://llm.example/v1/chat/completions"
    assert payload["model"] == "remote-model"
    assert headers == {"Authorization": "Bearer secret"}


async def test_enabled_llm_without_model_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "")
    provider = ProjectHindsightProviders(FakeEmbedder())

    with pytest.raises(ValueError, match="LLM_MODEL"):
        await provider.text("system", "user")


async def test_json_mode_explicitly_requests_json(monkeypatch):
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "remote-model")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    FakeClient.response = {"choices": [{"message": {"content": '{"facts": []}'}}]}
    assert await ProjectHindsightProviders(FakeEmbedder()).json(
        "Extract facts.", "Source"
    ) == {"facts": []}
    payload = FakeClient.request[1]
    assert payload["response_format"] == {"type": "json_object"}
    assert "json" in payload["messages"][0]["content"].lower()


async def test_json_with_usage_returns_provider_telemetry(monkeypatch):
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "remote-model")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    FakeClient.response = {
        "choices": [{"message": {"content": '{"actions": []}'}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }

    payload, usage = await ProjectHindsightProviders(FakeEmbedder()).json_with_usage(
        "Consolidate.", "Evidence"
    )

    assert payload == {"actions": []}
    assert usage["total_tokens"] == 15
