"""build_llm gating: disabled vs configured vs misconfigured."""

import pytest

from config.settings import settings
from src.agent.llm import ConfiguredLlmClient, build_llm


def test_build_llm_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings.llm, "base_url", "")
    assert build_llm() is None


def test_build_llm_builds_client_when_configured(monkeypatch):
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "remote-model")
    monkeypatch.setattr(settings.llm, "api_key", "secret")
    client = build_llm()
    assert isinstance(client, ConfiguredLlmClient)


def test_build_llm_raises_when_model_missing(monkeypatch):
    monkeypatch.setattr(settings.llm, "base_url", "https://llm.example/v1")
    monkeypatch.setattr(settings.llm, "model", "")
    with pytest.raises(ValueError, match="LLM_MODEL"):
        build_llm()
