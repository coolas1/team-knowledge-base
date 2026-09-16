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
                    metadata={
                        "scores": {"semantic": 0.73},
                        "source_type": "conversation",
                        "session_id": "session-1",
                        "turn_id": "turn-1",
                    },
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
    assert result.chunks[0].metadata["source_type"] == "conversation"
    assert result.chunks[0].metadata["session_id"] == "session-1"
    assert result.related_docs == [
        {
            "doc_id": "document-1",
            "title": "week.md",
            "relation_type": "",
            "reason": "",
        }
    ]
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


@pytest.mark.parametrize(
    ("route", "expected_chunk_ids"),
    [
        ("knowledge", ["document-memory"]),
        ("conversation", ["conversation-memory"]),
        ("mixed", ["document-memory", "conversation-memory"]),
    ],
)
async def test_adapter_preserves_source_groups_and_routes_compat_chunks(
    route, expected_chunk_ids
):
    document = KnowledgeSource(
        memory_id="document-memory",
        memory_type="world",
        doc_id="document-1",
        title="spec.md",
        chunk_text="document evidence",
        authority="document",
        source_group="document_evidence",
    )
    conversation = KnowledgeSource(
        memory_id="conversation-memory",
        memory_type="experience",
        doc_id="conversation-1",
        title="session",
        chunk_text="conversation context",
        authority="conversation",
        source_group="conversation_context",
    )
    service = FakeQueryService(
        KnowledgeQueryResult(
            strategy_used="recall",
            # Compatibility must not depend on this legacy, document-only field.
            sources=[document],
            document_evidence=[document],
            conversation_context=[conversation],
            route_used=route,
        )
    )

    result = await HindsightRecallAdapter(service).recall(
        RecallRequest(query="search", route=route)
    )

    assert [item.memory_id for item in result.chunks] == expected_chunk_ids
    assert [item.memory_id for item in result.document_evidence] == [
        "document-memory"
    ]
    assert [item.memory_id for item in result.conversation_context] == [
        "conversation-memory"
    ]
    assert result.document_evidence[0].metadata["source_group"] == "document_evidence"
    assert (
        result.conversation_context[0].metadata["source_group"]
        == "conversation_context"
    )


async def test_adapter_returns_conversation_only_without_document_sources():
    conversation = KnowledgeSource(
        memory_id="conversation-memory",
        memory_type="experience",
        doc_id="conversation-1",
        title="session",
        chunk_text="conversation context",
        authority="conversation",
        source_group="conversation_context",
    )
    service = FakeQueryService(
        KnowledgeQueryResult(
            strategy_used="recall",
            conversation_context=[conversation],
            route_used="conversation",
        )
    )

    result = await HindsightRecallAdapter(service).recall(
        RecallRequest(query="search", route="conversation")
    )

    assert result.document_evidence == []
    assert [item.memory_id for item in result.chunks] == ["conversation-memory"]
