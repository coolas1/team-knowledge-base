from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.scope import MemoryScope


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, results, *, bank_flags=None):
        self._results = iter(results)
        self.statements = []
        self.bank_flags = bank_flags or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement, _params=None):
        self.statements.append(statement)
        return _Rows(next(self._results))

    async def get(self, _model, bank_id):
        config = self.bank_flags.get(bank_id)
        return SimpleNamespace(config=config) if config is not None else None


def _memory(text: str):
    document_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    unit = SimpleNamespace(
        id=uuid.uuid4(),
        document_id=document_id,
        text=text,
        source_text=text,
        chunk_index=0,
        memory_type="world",
        context="",
        occurred_start=None,
        occurred_end=None,
        mentioned_at=now,
        metadata_json={},
        source_memory_ids=[],
        embedding=None,
        state="active",
    )
    document = SimpleNamespace(id=document_id, title="fixture.md")
    return unit, document


async def test_indexed_keyword_path_is_sql_bounded_and_skips_python_bm25(
    monkeypatch,
) -> None:
    unit, document = _memory("alpha indexed evidence")
    session = _Session(
        [
            [(unit.id, unit.text, 1, ["alpha", "indexed", "evidence"])],
            [(unit, document)],
        ],
        bank_flags={"bank-a": {"keyword_index_enabled": True}},
    )
    repository = PostgresMemoryRepository(
        lambda: session,
        keyword_index_enabled=True,
        keyword_candidate_limit=300,
        scope=MemoryScope(bank_id="bank-a"),
    )

    def forbidden_bm25(_query, _documents):
        raise AssertionError("indexed keyword reads must not invoke Python BM25")

    monkeypatch.setattr(repository, "_bm25", forbidden_bm25)
    results = await repository.keyword_search("alpha", 10, source_type="conversation")

    assert [item.id for item in results] == [str(unit.id)]
    assert results[0].metadata["keyword_index_mode"] == "indexed_sql"
    assert results[0].metadata["keyword_candidate_count"] == 1
    assert results[0].metadata["keyword_candidate_limit"] == 300
    sql = str(session.statements[0])
    assert "memory_units.lexical_tokens &&" in sql
    assert "unnest(memory_units.lexical_tokens)" in sql
    assert "LIMIT" in sql


async def test_disabled_keyword_index_preserves_python_bm25_rollback() -> None:
    alpha, alpha_document = _memory("alpha rollback evidence")
    beta, _ = _memory("unrelated material")
    session = _Session(
        [
            [(alpha.id, alpha.text), (beta.id, beta.text)],
            [(alpha, alpha_document)],
        ]
    )
    repository = PostgresMemoryRepository(
        lambda: session,
        keyword_index_enabled=False,
        keyword_candidate_limit=300,
    )

    results = await repository.keyword_search("alpha", 10, source_type="conversation")

    assert [item.id for item in results] == [str(alpha.id)]
    assert results[0].metadata["keyword_index_mode"] == "legacy_python"
    assert results[0].metadata["keyword_candidate_count"] == 2
    assert "lexical_tokens &&" not in str(session.statements[0])


async def test_bank_without_completed_keyword_migration_uses_legacy_path() -> None:
    alpha, document = _memory("alpha rollback evidence")
    session = _Session(
        [[(alpha.id, alpha.text)], [(alpha, document)]],
        bank_flags={"bank-b": {"keyword_index_enabled": False}},
    )
    repository = PostgresMemoryRepository(
        lambda: session,
        keyword_index_enabled=True,
        keyword_candidate_limit=300,
        scope=MemoryScope(bank_id="bank-b"),
    )

    results = await repository.keyword_search("alpha", 10, source_type="conversation")

    assert results[0].metadata["keyword_index_mode"] == "legacy_python"
    assert "lexical_tokens &&" not in str(session.statements[0])
