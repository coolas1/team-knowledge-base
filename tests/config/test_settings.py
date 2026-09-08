"""Settings sub-models: env-prefix wiring, defaults, nesting, mutability.

Each sub-model is instantiated with _env_file=None so tests are isolated
from the developer's real .env; env vars are set via monkeypatch.
"""

import pytest

from config.settings import (
    EmbeddingSettings,
    InfraSettings,
    LLMSettings,
    RerankerSettings,
)


def test_llm_settings_reads_prefixed_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example/v1")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    s = LLMSettings(_env_file=None)
    assert s.base_url == "https://api.example/v1"
    assert s.model == "some-model"
    assert s.api_key == "secret"


def test_llm_settings_defaults_to_disabled(monkeypatch):
    for var in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = LLMSettings(_env_file=None)
    assert s.base_url == ""
    assert s.model == ""
    assert s.api_key == ""
    assert s.enabled is False


def test_embedding_settings_reads_prefixed_env_and_ignores_other_groups(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://embed.example/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "embed-model")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.example/v1")
    monkeypatch.setenv("LLM_PROVIDER", "custom")  # stale var: must be ignored
    s = EmbeddingSettings(_env_file=None)
    assert s.base_url == "http://embed.example/v1"
    assert s.model == "embed-model"


def test_reranker_settings_defaults(monkeypatch):
    for var in (
        "RERANKER_PROVIDER",
        "RERANKER_BASE_URL",
        "RERANKER_MODEL",
        "RERANKER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    s = RerankerSettings(_env_file=None)
    assert s.provider == "none"
    assert s.base_url == ""
    assert s.model == "BAAI/bge-reranker-v2-m3"
    assert s.api_key == ""


def test_infra_settings_nests_submodels():
    s = InfraSettings(_env_file=None)
    assert isinstance(s.llm, LLMSettings)
    assert isinstance(s.embedding, EmbeddingSettings)
    assert isinstance(s.reranker, RerankerSettings)


def test_infra_settings_uploads_dir_default_and_env_override(monkeypatch, tmp_path):
    # Default keeps the relative dev-checkout behavior; UPLOADS_DIR points
    # the compose deployment at its absolute volume mount.
    monkeypatch.delenv("UPLOADS_DIR", raising=False)
    assert InfraSettings(_env_file=None).uploads_dir == "uploads"

    absolute = tmp_path / "uploads"
    monkeypatch.setenv("UPLOADS_DIR", str(absolute))
    assert InfraSettings(_env_file=None).uploads_dir == str(absolute)


def test_submodels_are_mutable_for_monkeypatch():
    s = LLMSettings(_env_file=None)
    s.base_url = "http://x/v1"
    assert s.base_url == "http://x/v1"


def test_require_model_returns_model_when_set():
    s = LLMSettings(_env_file=None, model="m1")
    assert s.require_model() == "m1"


def test_require_model_raises_when_empty():
    s = LLMSettings(_env_file=None)
    with pytest.raises(ValueError, match="LLM_MODEL"):
        s.require_model()
