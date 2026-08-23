from __future__ import annotations

from dataclasses import asdict

from src.engine.hindsight_components.adapter import HindsightKnowledgeBaseAdapter
from src.engine.hindsight_components.types import DocumentMemoryState
from src.engine.interface import IngestSource
from tests.conftest import FakeKnowledgeBase


class FakeStateReader:
    def __init__(self, states: dict[str, DocumentMemoryState] | None = None) -> None:
        self.states = states or {}
        self.single_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    async def document_state(self, document_id: str) -> DocumentMemoryState | None:
        self.single_calls.append(document_id)
        return self.states.get(document_id)

    async def document_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]:
        self.batch_calls.append(document_ids)
        return {
            document_id: self.states[document_id]
            for document_id in document_ids
            if document_id in self.states
        }


async def test_ingest_keeps_original_ref_and_marks_memory_pending():
    class PendingKnowledgeBase(FakeKnowledgeBase):
        async def ingest(self, source):
            ref = await super().ingest(source)
            ref.status = "pending"
            return ref

    kb = PendingKnowledgeBase()
    reader = FakeStateReader()
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    ref = await adapter.ingest(IngestSource(name="week.md", data=b"content"))

    assert ref.title == "week.md"
    assert ref.status == "pending"
    assert ref.memory_status == "pending"
    assert ref.memory_count == 0
    assert reader.single_calls == []


async def test_list_documents_enriches_original_items_in_one_batch():
    kb = FakeKnowledgeBase()
    first = await kb.ingest(IngestSource(name="one.md", data=b"one"))
    second = await kb.ingest(IngestSource(name="two.md", data=b"two"))
    reader = FakeStateReader(
        {
            first.id: DocumentMemoryState(
                document_id=first.id,
                status="indexed",
                memory_count=12,
                link_count=21,
            )
        }
    )
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    result = await adapter.list_documents()
    items = {item.id: asdict(item) for item in result["items"]}

    assert len(reader.batch_calls) == 1
    assert set(reader.batch_calls[0]) == {first.id, second.id}
    assert items[first.id]["memory_status"] == "indexed"
    assert items[first.id]["memory_count"] == 12
    assert items[first.id]["memory_link_count"] == 21
    assert items[second.id]["memory_status"] == "missing"


async def test_get_document_enriches_memory_failure_without_hiding_document():
    kb = FakeKnowledgeBase()
    ref = await kb.ingest(IngestSource(name="failed.md", data=b"content"))
    reader = FakeStateReader(
        {
            ref.id: DocumentMemoryState(
                document_id=ref.id,
                status="failed",
                error_msg="LLM unavailable",
                memory_count=3,
                link_count=2,
            )
        }
    )
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    document = await adapter.get_document(ref.id)

    assert document is not None
    assert document["title"] == "failed.md"
    assert document["memory_status"] == "failed"
    assert document["memory_error_msg"] == "LLM unavailable"
    assert document["memory_count"] == 3
    assert document["memory_link_count"] == 2


async def test_reingest_returns_latest_memory_state():
    kb = FakeKnowledgeBase()
    ref = await kb.ingest(IngestSource(name="week.md", data=b"content"))
    reader = FakeStateReader(
        {
            ref.id: DocumentMemoryState(
                document_id=ref.id,
                status="indexed",
                memory_count=8,
                link_count=13,
            )
        }
    )
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    result = await adapter.reingest(ref.id)

    assert result.status == "indexed"
    assert result.memory_status == "indexed"
    assert result.memory_count == 8
    assert result.memory_link_count == 13
    assert reader.single_calls == [ref.id]


async def test_edit_content_marks_existing_memory_pending():
    kb = FakeKnowledgeBase()
    ref = await kb.ingest(IngestSource(name="week.md", data=b"old"))
    reader = FakeStateReader(
        {
            ref.id: DocumentMemoryState(
                document_id=ref.id,
                status="indexed",
                memory_count=8,
            )
        }
    )
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    result = await adapter.edit_content(ref.id, "new")

    assert result.status == "pending"
    assert result.memory_status == "pending"
    assert kb.raw[ref.id] == b"new"
    assert reader.single_calls == [ref.id]


async def test_remove_keeps_original_document_lifecycle():
    kb = FakeKnowledgeBase()
    ref = await kb.ingest(IngestSource(name="remove.md", data=b"content"))
    adapter = HindsightKnowledgeBaseAdapter(kb, FakeStateReader())

    await adapter.remove(ref.id)

    assert ref.id not in kb.docs


async def test_state_read_failure_returns_unavailable(monkeypatch):
    kb = FakeKnowledgeBase()
    ref = await kb.ingest(IngestSource(name="week.md", data=b"content"))
    reader = FakeStateReader()

    async def fail(_document_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(reader, "document_state", fail)
    adapter = HindsightKnowledgeBaseAdapter(kb, reader)

    document = await adapter.get_document(ref.id)

    assert document is not None
    assert document["memory_status"] == "unavailable"
    assert document["title"] == "week.md"
