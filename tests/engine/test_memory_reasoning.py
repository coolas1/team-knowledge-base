import pytest

from config.settings import LLMSettings, settings
from src.engine.components.analyzer import Analyzer
from src.engine.components.llm_options import memory_identity, memory_options
from src.engine.components.store.file_summary import SummaryIdentity
from src.engine.hindsight_components.providers import ProjectHindsightProviders
from src.engine.hindsight_components.tests.test_providers import FakeClient

ARK = "https://ark.cn-beijing.volces.com/api/plan/v3"
MODEL = "doubao-seed-2.1-turbo"


@pytest.mark.parametrize("policy", ["disabled", "enabled"])
def test_explicit_policy_requires_supported_endpoint_and_model(policy):
    assert memory_options(MODEL, ARK, policy) == {"thinking": {"type": policy}}
    assert memory_options("deepseek-v4-flash", "https://api.deepseek.com/v1", policy)
    for model, url in [(MODEL, "https://other.example/v1"), ("unknown", ARK)]:
        with pytest.raises(ValueError, match="unsupported"):
            memory_options(model, url, policy)


def test_auto_preserves_existing_bounded_deepseek_behavior():
    assert memory_options(
        "deepseek-v4-flash", "https://proxy.example", "auto", bounded=True
    )
    assert memory_options(MODEL, ARK, "auto", bounded=True) == {}


def test_strategy_invalidates_generation_cache_identity():
    llm = LLMSettings(_env_file=None, model=MODEL, base_url=ARK)
    before = memory_identity(llm, bounded=True)
    llm.memory_thinking = "disabled"
    after = memory_identity(llm, bounded=True)
    assert before != after
    legacy = SummaryIdentity.for_text("text", "title", MODEL)
    current = SummaryIdentity.for_text("text", "title", MODEL, policy_version=after)
    assert legacy.key != current.key


@pytest.fixture
def ark(monkeypatch):
    monkeypatch.setattr(settings.llm, "model", MODEL)
    monkeypatch.setattr(settings.llm, "base_url", ARK)
    monkeypatch.setattr(settings.llm, "memory_thinking", "disabled")
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    FakeClient.response = {"choices": [{"message": {"content": '{"overview":"ok"}'}}]}


async def test_memory_calls_apply_policy_without_token_bound(ark):
    provider = ProjectHindsightProviders()
    for call in (provider.json, provider.json_with_usage, provider.text):
        await call("system", "user")
        assert FakeClient.request[1]["thinking"] == {"type": "disabled"}


async def test_summary_policy_does_not_affect_interactive_analyzer(ark):
    analyzer = Analyzer()
    await analyzer.summarize_document("source", "title")
    assert FakeClient.request[1]["thinking"] == {"type": "disabled"}
    await analyzer._call_openai_compatible("interactive editing")
    assert "thinking" not in FakeClient.request[1]


async def test_extraction_cache_reuses_only_matching_provider_policy():
    from src.engine.components.chunker import chunk_text
    from src.engine.hindsight_components.config import HindsightOptions
    from src.engine.hindsight_components.retain import RetainEngine
    from src.engine.hindsight_components.tests.fakes import (
        FakeProviders,
        FakeRepository,
    )
    from src.engine.hindsight_components.types import RetainInput

    provider = FakeProviders()
    provider.extraction_identity = "model:disabled"
    engine = RetainEngine(FakeRepository(), provider, HindsightOptions())
    value = RetainInput(
        document_id="test", title="test", content="Alice ran a survey", file_type="text"
    )
    chunks = chunk_text(value.content)
    _, _, cache = await engine._extract_facts(value, chunks)
    await engine._extract_facts(value, chunks, cache)
    assert len(provider.json_calls) == 1
    provider.extraction_identity = "model:enabled"
    await engine._extract_facts(value, chunks, cache)
    assert len(provider.json_calls) == 2


async def test_invalid_policy_does_not_degrade_into_empty_facts(monkeypatch):
    from src.engine.components.chunker import chunk_text
    from src.engine.hindsight_components.config import HindsightOptions
    from src.engine.hindsight_components.retain import RetainEngine
    from src.engine.hindsight_components.tests.fakes import FakeRepository
    from src.engine.hindsight_components.types import RetainInput

    monkeypatch.setattr(settings.llm, "model", "unknown")
    monkeypatch.setattr(settings.llm, "memory_thinking", "disabled")
    value = RetainInput(
        document_id="test", title="test", content="source", file_type="text"
    )
    engine = RetainEngine(
        FakeRepository(), ProjectHindsightProviders(), HindsightOptions()
    )
    with pytest.raises(ValueError, match="unsupported"):
        await engine._extract_facts(value, chunk_text(value.content))
