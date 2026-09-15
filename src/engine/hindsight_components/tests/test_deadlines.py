from __future__ import annotations

import asyncio
import logging

import pytest

from config.settings import InfraSettings
from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.deadlines import DeadlineBudget
from src.engine.hindsight_components.errors import (
    DeepSearchUnavailableError,
)
from src.engine.hindsight_components.recall import RecallEngine

from .fakes import FakeProviders, FakeRepository, candidate


def test_deadline_defaults_and_validation() -> None:
    options = HindsightOptions()
    assert options.deep_total_timeout_seconds == 45
    assert options.query_analysis_timeout_seconds == 8
    assert options.query_embedding_timeout_seconds == 10
    assert options.retrieval_arm_timeout_seconds == 5
    assert options.rerank_timeout_seconds == 12
    assert options.keyword_candidate_limit == 300

    configured = InfraSettings(
        _env_file=None,
        hindsight_deep_total_timeout_seconds=30,
        hindsight_rerank_candidate_limit=17,
    )
    assert configured.hindsight_deep_total_timeout_seconds == 30
    assert configured.hindsight_rerank_candidate_limit == 17
    with pytest.raises(ValueError, match="positive"):
        HindsightOptions(rerank_total_chars=0)
    with pytest.raises(ValueError):
        InfraSettings(_env_file=None, hindsight_retrieval_arm_timeout_seconds=0)


def test_monotonic_budget_caps_phases_by_remaining_time() -> None:
    now = [100.0]
    budget = DeadlineBudget(5.0, clock=lambda: now[0])
    assert budget.phase_timeout(8.0) == 5.0
    now[0] += 3.25
    assert budget.phase_timeout(8.0) == pytest.approx(1.75)
    assert budget.elapsed_ms() == 3250
    now[0] += 2
    assert budget.phase_timeout(1.0) == 0


async def test_analysis_timeout_degrades_to_semantic_and_keyword() -> None:
    class Providers(FakeProviders):
        async def json(self, system, user, *, timeout=600):
            if system.startswith("Analyze"):
                await asyncio.sleep(1)
            return await super().json(system, user, timeout=timeout)

    repository = FakeRepository()
    result = await RecallEngine(
        repository,
        Providers(),
        HindsightOptions(query_analysis_timeout_seconds=0.01),
    ).recall("compare the project")

    assert repository.calls["semantic"] == 1
    assert repository.calls["keyword"] == 1
    assert repository.calls["graph"] == 0
    assert repository.calls["temporal"] == 0
    assert result.trace["degraded"] is True
    assert (
        result.trace["phase_outcomes"]["query_analysis_llm"]["outcome"] == "timed_out"
    )


async def test_embedding_failure_keeps_non_vector_arms() -> None:
    class Providers(FakeProviders):
        async def embed(self, texts, *, timeout=None):
            raise RuntimeError("embedding endpoint secret")

    repository = FakeRepository()
    result = await RecallEngine(repository, Providers(), HindsightOptions()).recall(
        "Alice in 2024"
    )

    assert repository.calls["semantic"] == 0
    assert repository.calls["keyword"] == 1
    assert repository.calls["graph"] == 1
    assert repository.calls["temporal"] == 1
    assert (
        result.trace["phase_outcomes"]["query_embedding"]["category"] == "RuntimeError"
    )
    assert "secret" not in str(result.trace)


async def test_rerank_timeout_uses_deterministic_rrf() -> None:
    class Providers(FakeProviders):
        async def json(self, system, user, *, timeout=600):
            if system.startswith("Rank memories"):
                await asyncio.sleep(1)
            return await super().json(system, user, timeout=timeout)

    result = await RecallEngine(
        FakeRepository(),
        Providers(),
        HindsightOptions(rerank_timeout_seconds=0.01),
    ).recall("Alice project")

    assert result.results
    assert result.trace["ranking_method"] == "rrf"
    assert result.trace["phase_outcomes"]["neural_rerank_llm"]["outcome"] == "timed_out"


async def test_adaptive_simple_query_skips_analysis_graph_temporal_and_rerank() -> None:
    repository = FakeRepository()
    providers = FakeProviders()
    result = await RecallEngine(
        repository,
        providers,
        HindsightOptions(adaptive_deep_search_enabled=True),
    ).recall("autonomous driving algorithm")

    phases = result.trace["phase_outcomes"]
    assert phases["query_analysis_llm"]["category"] == "adaptive_simple_query"
    assert phases["graph_expansion"]["category"] == "not_required"
    assert phases["temporal_search"]["category"] == "not_required"
    assert phases["neural_rerank_llm"]["outcome"] in {"skipped", "succeeded"}
    assert repository.calls["graph"] == 0
    assert repository.calls["temporal"] == 0


async def test_adaptive_temporal_and_comparison_queries_start_needed_phases() -> None:
    temporal_repo = FakeRepository()
    await RecallEngine(
        temporal_repo,
        FakeProviders(),
        HindsightOptions(adaptive_deep_search_enabled=True),
    ).recall("What changed after 2024?")
    assert temporal_repo.calls["temporal"] == 1

    comparison_repo = FakeRepository()
    await RecallEngine(
        comparison_repo,
        FakeProviders(),
        HindsightOptions(adaptive_deep_search_enabled=True),
    ).recall("Compare project A versus project B")
    assert comparison_repo.calls["graph"] == 1


async def test_all_failed_arms_raise_typed_unavailable() -> None:
    class Repository(FakeRepository):
        async def semantic_search(self, *args, **kwargs):
            raise RuntimeError("semantic down")

        async def keyword_search(self, *args, **kwargs):
            raise RuntimeError("keyword down")

        async def graph_search(self, *args, **kwargs):
            raise RuntimeError("graph down")

        async def temporal_search(self, *args, **kwargs):
            raise RuntimeError("temporal down")

    with pytest.raises(DeepSearchUnavailableError) as caught:
        await RecallEngine(Repository(), FakeProviders(), HindsightOptions()).recall(
            "compare"
        )
    assert caught.value.code == "deep_search_unavailable"
    assert caught.value.as_payload()["error"]["search_id"]


async def test_deep_failure_attempts_document_fallback_once() -> None:
    class Repository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.keyword_attempts = 0

        async def semantic_search(self, *args, **kwargs):
            raise RuntimeError("semantic down")

        async def keyword_search(self, *args, **kwargs):
            self.keyword_attempts += 1
            if self.keyword_attempts == 1:
                raise RuntimeError("keyword transient")
            return [candidate("fallback", "document evidence", keyword=1.0)]

        async def graph_search(self, *args, **kwargs):
            raise RuntimeError("graph down")

        async def temporal_search(self, *args, **kwargs):
            raise RuntimeError("temporal down")

    repository = Repository()
    result = await RecallEngine(
        repository, FakeProviders(), HindsightOptions()
    ).recall("document evidence")

    assert repository.keyword_attempts == 2
    assert result.trace["fallback"] == "document_index"
    assert result.trace["ranking_method"] == "rrf"
    assert result.trace["phase_outcomes"]["document_index_fallback"]["outcome"] == "succeeded"


async def test_cancellation_stops_every_active_retrieval_arm() -> None:
    class Repository(FakeRepository):
        def __init__(self):
            super().__init__()
            self.started = 0
            self.cancelled = 0
            self.all_started = asyncio.Event()

        async def _wait(self):
            self.started += 1
            if self.started == 4:
                self.all_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled += 1

        async def semantic_search(self, *args, **kwargs):
            await self._wait()

        async def keyword_search(self, *args, **kwargs):
            await self._wait()

        async def graph_search(self, *args, **kwargs):
            await self._wait()

        async def temporal_search(self, *args, **kwargs):
            await self._wait()

    repository = Repository()
    pending = asyncio.create_task(
        RecallEngine(repository, FakeProviders(), HindsightOptions()).recall("compare")
    )
    await asyncio.wait_for(repository.all_started.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert repository.cancelled == 4


async def test_rerank_payload_is_bounded_and_diagnostics_are_sanitized(caplog) -> None:
    secret_query = "private-query-value"

    class Repository(FakeRepository):
        async def semantic_search(self, *args, **kwargs):
            return [
                candidate("first", "x" * 100, semantic=0.9),
                candidate("second", "y" * 100, semantic=0.8),
            ]

        async def keyword_search(self, *args, **kwargs):
            return []

    providers = FakeProviders()
    caplog.set_level(logging.INFO)
    result = await RecallEngine(
        Repository(),
        providers,
        HindsightOptions(
            rerank_candidate_limit=1,
            rerank_text_limit_chars=10,
            rerank_total_chars=32,
        ),
    ).recall(secret_query, search_id="search-test")

    rerank_prompt = next(user for user in providers.json_users if "MEMORIES" in user)
    assert "first:" in rerank_prompt
    assert "second:" not in rerank_prompt
    assert result.trace["rerank_truncated"] is True
    assert result.trace["search_id"] == "search-test"
    assert secret_query not in caplog.text
    records = [record for record in caplog.records if hasattr(record, "search_id")]
    assert records and all(record.search_id == "search-test" for record in records)
