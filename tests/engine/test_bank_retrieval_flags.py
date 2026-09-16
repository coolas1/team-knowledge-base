from types import SimpleNamespace

from src.engine.hindsight_components import file_chunk_recall
from src.engine.hindsight_components.types import RecallFilter
from src.engine.scope import MemoryScope


class _Rows:
    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self, configs):
        self.configs = configs
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, bank_id):
        config = self.configs.get(bank_id)
        return SimpleNamespace(config=config) if config is not None else None

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


async def test_hierarchy_is_enabled_per_bank_after_migration(monkeypatch) -> None:
    from config.settings import settings

    session = _Session(
        {
            "bank-a": {"hierarchical_retrieval_enabled": True},
            # A partial parent backfill must not switch bank B's reads.
            "bank-b": {"hierarchical_retrieval_enabled": False},
        }
    )
    hierarchical_calls = []

    async def fake_hierarchical(_sessions, scope, *_args):
        hierarchical_calls.append(scope.bank_id)
        return ["hierarchical"]

    monkeypatch.setattr(settings, "hindsight_hierarchical_retrieval_enabled", True)
    monkeypatch.setattr(
        file_chunk_recall, "_search_hierarchical_chunks", fake_hierarchical
    )

    bank_a = await file_chunk_recall.search_file_chunks(
        lambda: session,
        MemoryScope(bank_id="bank-a"),
        [0.0],
        5,
        "upload",
        RecallFilter(),
    )
    bank_b = await file_chunk_recall.search_file_chunks(
        lambda: session,
        MemoryScope(bank_id="bank-b"),
        [0.0],
        5,
        "upload",
        RecallFilter(),
    )

    assert bank_a == ["hierarchical"]
    assert bank_b == []
    assert hierarchical_calls == ["bank-a"]
    assert "document_retrieval" not in str(session.statements[-1]).lower()
