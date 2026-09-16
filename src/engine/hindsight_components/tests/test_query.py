from __future__ import annotations

import logging
import pytest

from src.engine.hindsight_components.query import HindsightQueryService
from src.engine.hindsight_components.types import RecallResult, ReflectResult
from src.engine.interface import KnowledgeQueryRequest

from src.engine.hindsight_components.tests.fakes import candidate


class FakeCore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int | None]] = []
        self.filters = []
        self.item = candidate("memory-a", "atomic evidence")
        self.item.final_score = 0.9

    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        filters=None,
    ) -> RecallResult:
        self.calls.append(("recall", query, mode, top_k))
        self.filters.append(filters)
        return RecallResult(
            results=[self.item],
            chunks={
                f"{self.item.document_id}_{self.item.chunk_index}": {
                    "text": "original source chunk"
                }
            },
            entities={"Alice": {"canonical_name": "Alice"}},
            trace={"mode": mode},
        )

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        filters=None,
    ) -> ReflectResult:
        self.calls.append(("reflect", query, mode, top_k))
        return ReflectResult(
            text="grounded answer [memory-a]",
            based_on={
                "world": [self.item.as_evidence()],
                "directives": [{"id": "directive-1", "content": "be concise"}],
                "mental_models": [{"id": "model-1", "content": "project model"}],
            },
            tool_trace=[{"tool": "recall", "iteration": 1}],
        )


async def test_auto_uses_reflect_when_answer_is_requested() -> None:
    core = FakeCore()
    service = HindsightQueryService(core)

    result = await service.query(
        KnowledgeQueryRequest(
            query="compare progress",
            strategy="auto",
            mode="fast",
            top_k=5,
            needs_answer=True,
        )
    )

    assert core.calls == [("reflect", "compare progress", "fast", 5)]
    assert result.strategy_used == "reflect"
    assert result.answer == "grounded answer [memory-a]"
    assert [source.memory_id for source in result.sources] == ["memory-a"]
    assert "tool_trace" in result.trace


async def test_auto_uses_recall_for_raw_context() -> None:
    core = FakeCore()
    service = HindsightQueryService(core)

    result = await service.query(
        KnowledgeQueryRequest(query="find progress", needs_answer=False, top_k=3)
    )

    assert core.calls == [("recall", "find progress", "deep", 3)]
    assert result.strategy_used == "recall"
    assert result.answer is None
    assert result.sources[0].chunk_text == "original source chunk"
    assert result.related_entities[0]["canonical_name"] == "Alice"
    assert result.trace["mode"] == "deep"


async def test_query_serializes_conversation_provenance_in_source_metadata() -> None:
    core = FakeCore()
    core.item.source_type = "conversation"
    core.item.session_id = "session-1"
    core.item.turn_id = "turn-1"
    core.item.metadata.update(
        {
            "source_type": "conversation",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }
    )

    result = await HindsightQueryService(core).query(
        KnowledgeQueryRequest(
            query="remembered preference",
            strategy="recall",
            route="conversation",
            include=("chunks", "entities", "based_on"),
        )
    )

    assert result.sources == []
    assert result.conversation_context[0].metadata["source_type"] == "conversation"
    assert result.conversation_context[0].metadata["session_id"] == "session-1"
    # based_on is opt-in and compact; provenance survives, evidence text does
    # not repeat.
    grouped = result.based_on["world"][0]
    assert grouped["turn_id"] == "turn-1"
    assert grouped["session_id"] == "session-1"
    assert "text" not in grouped


async def test_explicit_strategy_overrides_answer_purpose() -> None:
    core = FakeCore()
    service = HindsightQueryService(core)

    await service.query(
        KnowledgeQueryRequest(
            query="raw evidence", strategy="recall", needs_answer=True
        )
    )
    await service.query(
        KnowledgeQueryRequest(
            query="grounded answer", strategy="reflect", needs_answer=False
        )
    )

    assert [call[0] for call in core.calls] == ["recall", "reflect"]


async def test_extended_recall_request_builds_bounded_filter() -> None:
    class Core(FakeCore):
        filter = None

        async def recall(self, query, *, mode="deep", top_k=None, filters=None):
            self.filter = filters
            return await super().recall(query, mode=mode, top_k=top_k)

    core = Core()
    await HindsightQueryService(core).query(
        KnowledgeQueryRequest(
            query="current preferences",
            strategy="recall",
            memory_types=("observation",),
            tags=("user:1",),
            tags_match="all_strict",
            min_scores={"semantic": 0.6},
            max_tokens=100,
        )
    )

    assert core.filter.memory_types == ("observation",)
    assert core.filter.tags.matches(("user:1",))
    assert not core.filter.tags.matches(())
    assert core.filter.min_scores == {"semantic": 0.6}
    assert core.filter.max_tokens == 100


async def test_default_route_filters_to_uploaded_documents() -> None:
    core = FakeCore()
    result = await HindsightQueryService(core).query(
        KnowledgeQueryRequest(query="policy", strategy="recall")
    )

    assert core.filters[0].source_types == ("upload",)
    assert result.route_used == "knowledge"
    assert result.conversation_context == []


async def test_mixed_route_uses_independent_bounded_source_pools() -> None:
    class MixedCore(FakeCore):
        async def recall(self, query, *, mode="deep", top_k=None, filters=None):
            self.calls.append(("recall", query, mode, top_k))
            self.filters.append(filters)
            item = candidate(f"{filters.source_types[0]}-memory", "evidence")
            item.source_type = filters.source_types[0]
            item.final_score = 0.9
            return RecallResult(results=[item], chunks={}, entities={}, trace={})

    core = MixedCore()
    result = await HindsightQueryService(core).query(
        KnowledgeQueryRequest(query="continue from policy", route="mixed", top_k=5)
    )

    assert [call[3] for call in core.calls] == [4, 1]
    assert {value.source_types for value in core.filters} == {
        ("upload",),
        ("conversation",),
    }
    assert len(result.document_evidence) == 1
    assert len(result.conversation_context) == 1
    assert result.sources == result.document_evidence


async def test_mixed_top_one_never_exceeds_total_quota() -> None:
    core = FakeCore()
    await HindsightQueryService(core).query(
        KnowledgeQueryRequest(query="mixed", route="mixed", top_k=1)
    )
    assert core.calls == [("recall", "mixed", "deep", 1)]


async def test_mixed_kill_switch_falls_back_to_document_search() -> None:
    core = FakeCore()
    service = HindsightQueryService(core, mixed_source_search_enabled=False)
    result = await service.query(
        KnowledgeQueryRequest(query="mixed", route="mixed", strategy="recall")
    )

    assert len(core.calls) == 1
    assert core.filters[0].source_types == ("upload",)
    assert result.route_used == "knowledge"
    assert result.trace["requested_route"] == "mixed"
    assert result.trace["fallback"] == "mixed_source_search_disabled"


async def test_query_diagnostics_are_structured_and_do_not_log_content(caplog) -> None:
    caplog.set_level(logging.INFO)
    secret = "query-secret-that-must-not-be-logged"
    await HindsightQueryService(FakeCore()).query(
        KnowledgeQueryRequest(query=secret, strategy="recall")
    )

    assert secret not in caplog.text
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "hindsight.knowledge_query.complete"
    )
    assert record.query_route == "knowledge"
    assert record.conversation_leakage_count == 0
    assert record.source_pool_counts == {"upload": 1, "conversation": 0}
    assert hasattr(record, "document_coverage")


async def test_query_validates_input() -> None:
    service = HindsightQueryService(FakeCore())

    with pytest.raises(ValueError, match="empty"):
        await service.query(KnowledgeQueryRequest(query=" "))
    with pytest.raises(ValueError, match="top_k"):
        await service.query(KnowledgeQueryRequest(query="q", top_k=0))


def test_adaptive_reflection_exposes_only_actual_citations() -> None:
    item = candidate("retrieved", "retrieved but unused")
    reflected = ReflectResult(
        text="insufficient",
        based_on={"world": [item.as_evidence()], "actual_citations": []},
        tool_trace=[],
        actual_citations=[],
    )

    assert HindsightQueryService._sources_from_reflection(reflected) == []


async def test_build_query_service_accepts_repository(monkeypatch):
    """build_query_service should accept an optional repository parameter."""
    from src.engine.hindsight_components.query import build_query_service
    from src.engine.hindsight_components.tests.fakes import FakeRepository

    repo = FakeRepository()

    # Mock HindsightService so construction doesn't require external providers
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.HindsightService",
        lambda r, p, options=None: object(),
    )

    service = build_query_service(repository=repo)
    assert service is not None


def test_build_query_service_constructs_default_repository(monkeypatch):
    """Runtime construction passes only repository-owned settings."""
    from config.settings import settings
    from src.engine.hindsight_components import query
    from src.engine.hindsight_components.tests.fakes import FakeRepository

    captured = {}

    def repository_factory(*, keyword_index_enabled, keyword_candidate_limit):
        captured.update(
            keyword_index_enabled=keyword_index_enabled,
            keyword_candidate_limit=keyword_candidate_limit,
        )
        return FakeRepository()

    monkeypatch.setattr(query, "PostgresMemoryRepository", repository_factory)
    monkeypatch.setattr(
        query,
        "HindsightService",
        lambda repository, providers, options=None: object(),
    )

    service = query.build_query_service()

    assert service is not None
    assert captured == {
        "keyword_index_enabled": settings.hindsight_keyword_index_enabled,
        "keyword_candidate_limit": settings.hindsight_keyword_candidate_limit,
    }
