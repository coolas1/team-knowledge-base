from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.utils import (
    document_lock_key,
    lexical_tokens,
    normalize_entity,
)


def test_bm25_ranks_matching_english_and_chinese_documents() -> None:
    documents = [
        "Alice completed the coral survey",
        "Bob prepared the budget",
        "项目完成了知识库检索测试",
    ]

    english = PostgresMemoryRepository._bm25("coral survey", documents)
    chinese = PostgresMemoryRepository._bm25("知识库检索", documents)

    assert english[0] > english[1]
    assert chinese[2] > chinese[0]


def test_candidate_mapping_preserves_provenance_and_scores() -> None:
    memory_id = uuid.uuid4()
    document_id = uuid.uuid4()
    unit = SimpleNamespace(
        id=memory_id,
        document_id=document_id,
        text="atomic fact",
        source_text="source chunk",
        chunk_index=2,
        memory_type="world",
        context="Knowledge-base document: week.md",
        occurred_start=None,
        occurred_end=None,
        metadata_json={"file_type": "markdown"},
        source_memory_ids=[],
        embedding=[1.0, 0.0],
    )

    candidate = PostgresMemoryRepository._candidate(
        unit, SimpleNamespace(title="week.md"), semantic_score=0.9
    )

    assert candidate.id == str(memory_id)
    assert candidate.document_id == str(document_id)
    assert candidate.title == "week.md"
    assert candidate.semantic_score == 0.9
    assert candidate.source_text == "source chunk"


def test_normalization_tokens_and_advisory_lock_are_stable() -> None:
    document_id = uuid.uuid4()

    assert normalize_entity(" Alice / TKB ") == "alice tkb"
    assert "knowledge" in lexical_tokens("Knowledge retrieval")
    assert lexical_tokens("知识库")
    assert document_lock_key(document_id) == document_lock_key(document_id)
    assert -(1 << 63) <= document_lock_key(document_id) < (1 << 63)


def test_document_state_mapping_preserves_counts_and_error() -> None:
    document_id = uuid.uuid4()
    updated_at = datetime.now(timezone.utc)
    state = PostgresMemoryRepository._state_from_row(
        SimpleNamespace(
            document_id=document_id,
            status="failed",
            error_msg="LLM unavailable",
            memory_count=12,
            link_count=21,
            updated_at=updated_at,
        )
    )

    assert state.document_id == str(document_id)
    assert state.status == "failed"
    assert state.error_msg == "LLM unavailable"
    assert state.memory_count == 12
    assert state.link_count == 21
    assert state.updated_at == updated_at.isoformat()


def test_enqueue_graph_event_uses_same_callers_session() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.added = []

        def add(self, value) -> None:
            self.added.append(value)

    session = FakeSession()
    document_id = uuid.uuid4()

    event = PostgresMemoryRepository._enqueue_graph_event(
        session, document_id, "replace"
    )

    assert session.added == [event]
    assert event.document_id == document_id
    assert event.operation == "replace"
    assert event.status is None or event.status == "pending"


def test_enqueue_graph_event_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError, match="unsupported graph operation"):
        PostgresMemoryRepository._enqueue_graph_event(
            SimpleNamespace(add=lambda value: None),
            uuid.uuid4(),
            "truncate",
        )


def test_graph_projection_maps_postgres_memory_rows() -> None:
    document_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    target_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    occurred_at = datetime.now(timezone.utc)
    document = SimpleNamespace(
        id=document_id,
        title="week.md",
        file_type="markdown",
        overview="weekly progress",
    )
    memory = SimpleNamespace(
        id=memory_id,
        document_id=document_id,
        memory_type="world",
        text="TKB uses Hindsight",
        context="weekly report",
        chunk_index=2,
        occurred_start=occurred_at,
        occurred_end=None,
        confidence=0.9,
        source_memory_ids=[target_id],
        tags=["tkb"],
        metadata_json={"file_type": "markdown"},
    )
    entity = SimpleNamespace(
        id=entity_id,
        canonical_name="TKB",
        normalized_name="tkb",
        entity_type="Project",
        metadata_json={},
    )
    mention = SimpleNamespace(memory_id=memory_id, entity_id=entity_id, role="subject")
    link = SimpleNamespace(
        source_memory_id=memory_id,
        target_memory_id=target_id,
        link_type="semantic",
        weight=0.8,
        metadata_json={"source": "retain"},
    )

    result = PostgresMemoryRepository._graph_projection(
        document,
        [memory],
        [(mention, entity)],
        [link],
    )

    assert result.document.id == str(document_id)
    assert result.memories[0].occurred_start == occurred_at.isoformat()
    assert result.memories[0].source_memory_ids == (str(target_id),)
    assert result.entities[0].normalized_name == "tkb"
    assert result.mentions[0].role == "subject"
    assert result.links[0].link_type == "semantic"


async def test_dependent_graph_documents_include_observations_and_inbound_links():
    observation_document = uuid.uuid4()
    link_document = uuid.uuid4()

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        async def scalars(self, statement):
            self.calls += 1
            if self.calls == 1:
                return [observation_document]
            return [link_document, observation_document]

    session = FakeSession()
    result = await PostgresMemoryRepository._dependent_graph_documents(
        session,
        [uuid.uuid4()],
        exclude_document_id=uuid.uuid4(),
    )

    assert result == {observation_document, link_document}
    assert session.calls == 2


async def test_dependent_graph_documents_skip_queries_for_empty_memory_set():
    class FakeSession:
        async def scalars(self, statement):
            raise AssertionError("no query expected")

    result = await PostgresMemoryRepository._dependent_graph_documents(
        FakeSession(),
        [],
        exclude_document_id=uuid.uuid4(),
    )

    assert result == set()
