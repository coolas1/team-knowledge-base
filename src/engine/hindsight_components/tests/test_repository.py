from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.memory_identity import (
    canonical_memory_text,
    memory_content_fingerprint,
)
from src.engine.hindsight_components.types import RecallFilter
from src.engine.hindsight_components.utils import (
    document_lock_key,
    lexical_tokens,
    normalize_entity,
)

from .fakes import candidate


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


def test_memory_content_fingerprint_is_canonical_and_type_scoped() -> None:
    assert canonical_memory_text("  Prefers， concise ANSWERS! ") == (
        "prefers concise answers"
    )
    left = memory_content_fingerprint("Prefers concise answers!", "preference", "user")
    assert left == memory_content_fingerprint(
        "  PREFERS concise answers ", "preference", "user"
    )
    assert left != memory_content_fingerprint(
        "Prefers concise answers!", "state", "user"
    )


def test_bounded_prefilter_preserves_frozen_multilingual_top_results() -> None:
    documents = [
        "TKB deep search timeout handling",
        "TKB deployment notes",
        "知识库深度检索超时处理",
        "知识库部署说明",
        "unrelated lunch menu",
        "无关的午餐菜单",
    ]
    for query in ("TKB search timeout", "知识库检索超时"):
        legacy_scores = PostgresMemoryRepository._bm25(query, documents)
        legacy = sorted(
            range(len(documents)), key=lambda index: legacy_scores[index], reverse=True
        )
        query_terms = set(lexical_tokens(query))
        candidates = [
            index
            for index, document in enumerate(documents)
            if query_terms.intersection(lexical_tokens(document))
        ][:300]
        candidate_scores = PostgresMemoryRepository._bm25(
            query, [documents[index] for index in candidates]
        )
        indexed = [
            item[0]
            for item in sorted(
                zip(candidates, candidate_scores, strict=True),
                key=lambda item: item[1],
                reverse=True,
            )
        ]
        assert indexed[0] == legacy[0]


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
        metadata_json={
            "file_type": "conversation",
            "source_type": "conversation",
            "session_id": "session-1",
            "turn_id": "turn-1",
        },
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
    assert candidate.source_type == "conversation"
    assert candidate.session_id == "session-1"
    assert candidate.turn_id == "turn-1"
    assert candidate.as_evidence()["session_id"] == "session-1"


def test_lifecycle_values_validate_and_normalize_metadata() -> None:
    superseded_by = uuid.uuid4()
    draft = SimpleNamespace(
        metadata={
            "source_type": "conversation",
            "origin": "user",
            "authority": "user_confirmed",
            "retention_policy_version": 3,
            "confirmed_by_turn_id": "turn-1",
            "derived_from_evidence_ids": ["doc:2", "doc:1", "doc:2"],
            "expires_at": "2027-01-01T00:00:00Z",
            "lifecycle_state": "superseded",
            "superseded_by": str(superseded_by),
            "lifecycle_key": "User:Response-Style",
        }
    )

    values = PostgresMemoryRepository._lifecycle_values(draft)

    assert values["origin"] == "user"
    assert values["authority"] == "user_confirmed"
    assert values["policy_version"] == 3
    assert values["confirmed_by_turn_id"] == "turn-1"
    assert values["derived_from_evidence_ids"] == ["doc:1", "doc:2"]
    assert values["expires_at"].isoformat() == "2027-01-01T00:00:00+00:00"
    assert values["lifecycle_state"] == "superseded"
    assert values["superseded_by"] == superseded_by
    assert values["lifecycle_key"] == "user:response-style"


def test_user_confirmed_authority_requires_confirming_turn() -> None:
    draft = SimpleNamespace(
        metadata={
            "source_type": "conversation",
            "origin": "user",
            "authority": "user_confirmed",
        }
    )

    with pytest.raises(ValueError, match="requires confirmed_by_turn_id"):
        PostgresMemoryRepository._lifecycle_values(draft)


@pytest.mark.parametrize(
    "metadata",
    [
        {"policy_version": 0},
        {"derived_from_evidence_ids": [""]},
        {"expires_at": "2027-01-01T00:00:00"},
        {"lifecycle_state": "active"},
        {"superseded_by": "not-a-uuid"},
        {"lifecycle_key": " "},
    ],
)
def test_lifecycle_values_reject_invalid_metadata(metadata) -> None:
    with pytest.raises((ValueError, TypeError)):
        PostgresMemoryRepository._lifecycle_values(SimpleNamespace(metadata=metadata))


async def test_all_retrieval_arms_filter_source_and_incomplete_conversations() -> None:
    class EmptyResult:
        def __iter__(self):
            return iter(())

        def all(self):
            return []

    class Session:
        def __init__(self) -> None:
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            self.statements.append(statement)
            return EmptyResult()

    session = Session()
    repository = PostgresMemoryRepository(lambda: session)

    await repository.semantic_search([0.0] * 768, 5, source_type="conversation")
    await repository.keyword_search("preference", 5, source_type="conversation")
    await repository.graph_search(["Alice"], 5, source_type="conversation")
    await repository.temporal_search(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        None,
        5,
        source_type="conversation",
    )

    assert len(session.statements) == 4
    compiled = [
        statement.compile(dialect=postgresql.dialect())
        for statement in session.statements
    ]
    assert all("conversation_memory_sources" in str(item) for item in compiled)
    assert all("metadata_json" in str(item) for item in compiled)
    assert all("conversation" in item.params.values() for item in compiled)


async def test_keyword_search_truncates_each_source_pool_separately(
    monkeypatch,
) -> None:
    """文件池字段加权分远高于记忆池 overlap 计数，不能共用一个 ``[:limit]``。

    记忆行按 lexical overlap/BM25 计数打分，文件行按字段加权分（标题命中
    可值数倍正文），量纲不同。合并后一起排序再截断，文件池会把记忆行整体
    挤出 keyword 臂，池内 RRF 根本看不到它们。
    """
    from src.engine.hindsight_components import file_chunk_recall
    from src.engine.hindsight_components import repository as repository_mod

    memory_ids = [uuid.uuid4() for _ in range(2)]

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class Session:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement, *_args):
            self.calls += 1
            if self.calls == 1:
                return Result([(mid, "alpha alpha alpha") for mid in memory_ids])
            # 真实数据库只返回 IN (score_by_id) 命中的行
            return Result(
                [
                    (SimpleNamespace(id=mid), SimpleNamespace(id="document"))
                    for mid in memory_ids
                ]
            )

    session = Session()
    repository = PostgresMemoryRepository(lambda: session)

    # 文件池：字段加权分 100 起步；记忆池：BM25 计数只有个位数
    files = [candidate(f"f{i}", "file", keyword=100.0 - i) for i in range(5)]

    async def fake_file_keywords(*_args, **_kwargs):
        return files

    monkeypatch.setattr(
        file_chunk_recall, "search_file_keywords", fake_file_keywords
    )
    monkeypatch.setattr(
        repository_mod.PostgresMemoryRepository,
        "_candidate",
        staticmethod(
            lambda unit, document, **scores: candidate(
                f"m{unit.id}", "memory", keyword=scores.get("keyword_score", 0.0)
            )
        ),
    )

    results = await repository.keyword_search("alpha", 2)

    pools = {item.id[0] for item in results}
    assert pools == {"m", "f"}, f"一个池被整体挤掉了: {[i.id for i in results]}"
    assert len(results) == 4  # 每个池各自保留 limit 条


async def test_indexed_keyword_search_limits_materialized_candidates() -> None:
    class EmptyResult:
        def all(self):
            return []

    class Session:
        def __init__(self) -> None:
            self.statement = None
            self.params = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _bank_id):
            return SimpleNamespace(config={"keyword_index_enabled": True})

        async def execute(self, statement, params):
            self.statement = statement
            self.params = params
            return EmptyResult()

    session = Session()
    repository = PostgresMemoryRepository(
        lambda: session,
        keyword_index_enabled=True,
        keyword_candidate_limit=300,
    )
    assert (
        await repository.keyword_search(
            "TKB 知识库", 50, filters=RecallFilter(include=())
        )
        == []
    )

    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    assert "lexical_tokens &&" in sql
    assert "unnest(memory_units.lexical_tokens)" in sql
    assert 300 in compiled.params.values()
    assert session.params["keyword_query_tokens"] == ["tkb", "知识", "识库"]


def test_candidate_coerces_numpy_embedding_to_native_floats() -> None:
    # pgvector returns embeddings as numpy arrays; list(arr) keeps numpy.float32
    # scalars, which FastAPI's jsonable_encoder cannot serialize (HTTP 500 on
    # every memory search endpoint).
    import numpy as np

    unit = SimpleNamespace(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        text="atomic fact",
        source_text="source chunk",
        chunk_index=0,
        memory_type="world",
        context="",
        occurred_start=None,
        occurred_end=None,
        metadata_json={},
        source_memory_ids=[],
        embedding=np.array([0.1, 0.2, 0.3], dtype=np.float32),
    )

    candidate = PostgresMemoryRepository._candidate(
        unit, SimpleNamespace(title="week.md"), semantic_score=0.9
    )

    assert candidate.embedding is not None
    assert len(candidate.embedding) == 3
    assert all(type(value) is float for value in candidate.embedding)
    assert candidate.embedding == pytest.approx([0.1, 0.2, 0.3], rel=1e-6)


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
                stage_results={},
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

    event = PostgresMemoryRepository(session_factory=lambda: None)._enqueue_graph_event(
        session, document_id, "replace"
    )

    assert session.added == [event]
    assert event.document_id == document_id
    assert event.operation == "replace"
    assert event.status is None or event.status == "pending"


def test_enqueue_graph_event_rejects_unknown_operation() -> None:
    with pytest.raises(ValueError, match="unsupported graph operation"):
        PostgresMemoryRepository(session_factory=lambda: None)._enqueue_graph_event(
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
    result = await PostgresMemoryRepository(
        session_factory=lambda: None
    )._dependent_graph_documents(
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

    result = await PostgresMemoryRepository(
        session_factory=lambda: None
    )._dependent_graph_documents(
        FakeSession(),
        [],
        exclude_document_id=uuid.uuid4(),
    )

    assert result == set()
