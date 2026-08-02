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
    monkeypatch.setattr(settings, "llm_provider", "todo")
    provider = ProjectHindsightProviders(FakeEmbedder())

    with pytest.raises(RuntimeError, match="LLM_PROVIDER=todo"):
        await provider.json("system", "user")


async def test_local_ollama_uses_native_chat_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.local:11434")
    monkeypatch.setattr(settings, "llm_model", "qwen3:14b")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    FakeClient.response = {"message": {"content": '{"ok": true}'}}
    provider = ProjectHindsightProviders(FakeEmbedder())

    assert await provider.json("system", "user") == {"ok": True}
    assert FakeClient.request is not None
    url, payload, _ = FakeClient.request
    assert url == "http://ollama.local:11434/api/chat"
    assert payload["format"] == "json"
    assert payload["think"] is False
    assert payload["options"] == {"temperature": 0, "num_predict": 4096}


async def test_network_model_uses_openai_compatible_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "llm_provider", "custom")
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings, "llm_model", "remote-model")
    monkeypatch.setattr(settings, "llm_api_key", "secret")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeClient)
    FakeClient.response = {"choices": [{"message": {"content": "answer"}}]}
    provider = ProjectHindsightProviders(FakeEmbedder())

    assert await provider.text("system", "user") == "answer"
    assert FakeClient.request is not None
    url, payload, headers = FakeClient.request
    assert url == "https://llm.example/v1/chat/completions"
    assert payload["model"] == "remote-model"
    assert headers == {"Authorization": "Bearer secret"}
