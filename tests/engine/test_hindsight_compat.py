import pytest

from src.engine.hindsight_components.compat import (
    HindsightRecallAdapter,
    resolve_recall_mode,
)
from src.engine.interface import (
    KnowledgeQueryResult,
    KnowledgeSource,
    RecallRequest,
)


class FakeQueryService:
    def __init__(self, result: KnowledgeQueryResult) -> None:
        self.result = result
        self.request = None

    async def query(self, request):
        self.request = request
        return self.result


@pytest.mark.parametrize(
    ("query", "needs_answer", "expected"),
    [
        ("TKB 是什么？", False, "fast"),
        ("比较最近三周的项目进展", False, "deep"),
        ("TKB 是什么？", True, "deep"),
    ],
)
def test_auto_mode_routes_by_query_purpose(query, needs_answer, expected):
    assert resolve_recall_mode(query, "auto", needs_answer=needs_answer) == expected


def test_explicit_mode_overrides_auto_routing():
    assert resolve_recall_mode("分析全部文档", "fast", needs_answer=True) == "fast"


async def test_adapter_maps_hindsight_sources_to_original_recall_contract():
    service = FakeQueryService(
        KnowledgeQueryResult(
            strategy_used="recall",
            sources=[
                KnowledgeSource(
                    memory_id="memory-1",
                    memory_type="world",
                    doc_id="document-1",
                    title="week.md",
                    chunk_text="Implemented the search adapter.",
                    score=0.91,
                    metadata={"scores": {"semantic": 0.73}},
                ),
                KnowledgeSource(
                    memory_id="memory-2",
                    memory_type="observation",
                    doc_id="document-1",
                    title="week.md",
                    chunk_text="The adapter preserves old fields.",
                    score=0.82,
                ),
            ],
            related_entities=[{"name": "TKB"}],
            based_on={"world": [{"id": "memory-1"}]},
            trace={"phase": "recall"},
        )
    )

    result = await HindsightRecallAdapter(service).recall(
        RecallRequest(query="TKB 是什么？", top_k=4)
    )

    assert service.request.strategy == "auto"
    assert service.request.mode == "fast"
    assert service.request.needs_answer is False
    assert result.chunks[0].doc_id == "document-1"
    assert result.chunks[0].memory_id == "memory-1"
    assert result.chunks[0].reranker_score == 0.91
    assert result.chunks[0].vector_score == 0.73
    assert result.related_docs == [{"doc_id": "document-1", "title": "week.md"}]
    assert result.related_entities == [{"name": "TKB"}]
    assert result.mode_used == "fast"
    assert result.strategy_used == "recall"
    assert result.trace == {"phase": "recall", "mode": "fast"}


async def test_adapter_uses_reflect_when_answer_is_requested():
    service = FakeQueryService(
        KnowledgeQueryResult(strategy_used="reflect", answer="Grounded answer")
    )

    result = await HindsightRecallAdapter(service).recall(
        RecallRequest(query="总结本周进展", needs_answer=True)
    )

    assert service.request.needs_answer is True
    assert service.request.mode == "deep"
    assert result.answer == "Grounded answer"
    assert result.strategy_used == "reflect"


@pytest.mark.parametrize(
    "recall_request",
    [
        RecallRequest(query="   "),
        RecallRequest(query="valid", top_k=0),
    ],
)
async def test_adapter_validates_original_recall_request(recall_request):
    service = FakeQueryService(KnowledgeQueryResult(strategy_used="recall"))
    with pytest.raises(ValueError):
        await HindsightRecallAdapter(service).recall(recall_request)
