import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from config.settings import settings
from src.engine.hindsight_components.file_chunk_recall import (
    _search_hierarchical_chunks,
    search_file_keywords,
)
from src.engine.hindsight_components.types import RecallFilter
from src.engine.scope import MemoryScope


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        return iter(self.rows)


class _Session:
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows(next(self.results))


async def test_dense_passage_window_applies_per_document_cap_in_sql(monkeypatch):
    document_id = uuid.uuid4()
    parent = SimpleNamespace(doc_id=document_id)
    document = SimpleNamespace(
        id=document_id,
        title="small relevant document",
        updated_at=datetime.now(timezone.utc),
    )
    session = _Session([[(parent, document, 0.9)], []])
    monkeypatch.setattr(settings, "hindsight_hybrid_safety_lane_enabled", False)
    monkeypatch.setattr(settings, "hindsight_max_passages_per_document", 2)

    await _search_hierarchical_chunks(
        lambda: session,
        MemoryScope(bank_id="bank-a"),
        [0.0],
        10,
        RecallFilter(),
    )

    sql = str(session.statements[1]).lower()
    assert "row_number() over (partition by chunks.doc_id" in sql
    assert "ranked_passages.passage_rank <=" in sql
    assert sql.index("ranked_passages.passage_rank <=") < sql.rindex("limit")


async def test_lexical_prefilter_applies_per_document_cap_before_global_limit():
    session = _Session([[]])

    results = await search_file_keywords(
        lambda: session,
        MemoryScope(bank_id="bank-a"),
        "autopilot",
        10,
        "upload",
        RecallFilter(),
        candidate_limit=100,
        scorer=lambda _query, documents: [1.0] * len(documents),
    )

    assert results == []
    sql = str(session.statements[0]).lower()
    assert "row_number() over (partition by chunks.doc_id" in sql
    assert "ranked_lexical.lexical_rank <=" in sql
    assert sql.index("ranked_lexical.lexical_rank <=") < sql.rindex("limit")
