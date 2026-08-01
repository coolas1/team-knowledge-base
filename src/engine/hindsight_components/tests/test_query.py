from __future__ import annotations

import pytest

from src.engine.hindsight_components.query import HindsightQueryService
from src.engine.hindsight_components.types import RecallResult, ReflectResult
from src.engine.interface import KnowledgeQueryRequest

from .fakes import candidate


class FakeCore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int | None]] = []
        self.item = candidate("memory-a", "atomic evidence")
        self.item.final_score = 0.9

    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult:
        self.calls.append(("recall", query, mode, top_k))
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


async def test_query_validates_input() -> None:
    service = HindsightQueryService(FakeCore())

    with pytest.raises(ValueError, match="empty"):
        await service.query(KnowledgeQueryRequest(query=" "))
    with pytest.raises(ValueError, match="top_k"):
        await service.query(KnowledgeQueryRequest(query="q", top_k=0))
