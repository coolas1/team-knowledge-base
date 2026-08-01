from __future__ import annotations

import uuid
from types import SimpleNamespace

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
