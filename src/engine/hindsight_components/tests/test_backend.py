from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from src.engine.hindsight_components.backend import HindsightBackend
from src.engine.hindsight_components.contracts import StoredDocument
from src.engine.interface import IngestSource, RecallRequest


class FakeExtractor:
    async def extract(
        self, name: str, data: bytes, path: str | None
    ) -> tuple[str, str]:
        return data.decode(), "markdown"


class FakeDocuments:
    def __init__(self) -> None:
        self.items: dict[str, StoredDocument] = {}

    async def create(
        self, *, title: str, file_type: str, raw_text: str
    ) -> StoredDocument:
        item = StoredDocument(
            id=str(uuid.uuid4()),
            title=title,
            file_type=file_type,
            status="pending",
            raw_text=raw_text,
        )
        self.items[item.id] = item
        return item

    async def get(self, doc_id: str) -> StoredDocument | None:
        return self.items.get(doc_id)

    async def set_status(
        self, doc_id: str, status: str, *, error_msg: str | None = None
    ) -> StoredDocument:
        item = replace(self.items[doc_id], status=status, error_msg=error_msg)
        self.items[doc_id] = item
        return item

    async def delete(self, doc_id: str) -> None:
        self.items.pop(doc_id, None)

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        file_type: str | None,
        status: str | None,
    ) -> tuple[int, list[StoredDocument]]:
        items = [
            item
            for item in self.items.values()
            if (not file_type or item.file_type == file_type)
            and (not status or item.status == status)
        ]
        return len(items), items[(page - 1) * page_size : page * page_size]


class FakeMemory:
    def __init__(self) -> None:
        self.retained: list[dict] = []
        self.deleted: list[str] = []
        self.mode: str | None = None

    async def retain_document(self, **kwargs) -> dict:
        self.retained.append(kwargs)
        return {"success": True}

    async def delete_document(
        self, document_id: str, *, missing_ok: bool = True
    ) -> bool:
        self.deleted.append(document_id)
        return True

    async def recall(self, query: str, mode: str = "deep") -> dict:
        self.mode = mode
        return {
            "results": [
                {
                    "id": "memory-1",
                    "text": "atomic fact",
                    "document_id": "doc-1",
                    "chunk_id": "doc-1_0",
                    "metadata": {"title": "week.md"},
                    "scores": {"final": 0.8, "reranker": 0.8, "semantic": 0.7},
                }
            ],
            "chunks": {"doc-1_0": {"text": "original source chunk"}},
            "entities": {"TKB": {"summary": "project"}},
        }

    async def entity_graph(self) -> dict:
        return {
            "nodes": [
                {"data": {"id": "1", "label": "TKB", "mentionCount": 2}},
                {"data": {"id": "2", "label": "Hindsight", "mentionCount": 1}},
            ],
            "edges": [
                {
                    "data": {
                        "source": "1",
                        "target": "2",
                        "linkType": "cooccurrence",
                        "weight": 1,
                    }
                }
            ],
        }

    async def get_entity_by_name(self, name: str) -> dict | None:
        return {"canonical_name": name, "metadata": {}, "observations": []}


@pytest.fixture
def dependencies() -> tuple[FakeMemory, FakeDocuments]:
    return FakeMemory(), FakeDocuments()


async def test_backend_satisfies_ingest_reingest_and_remove(dependencies) -> None:
    memory, documents = dependencies
    backend = HindsightBackend(memory, documents, extractor=FakeExtractor())

    created = await backend.ingest(IngestSource(name="week.md", data=b"weekly report"))
    assert created.status == "indexed"
    assert memory.retained[0]["document_id"] == created.id
    assert memory.retained[0]["content"] == "weekly report"

    rebuilt = await backend.reingest(created.id)
    assert rebuilt.status == "indexed"
    assert len(memory.retained) == 2

    await backend.remove(created.id)
    assert memory.deleted == [created.id]
    assert await backend.get_document(created.id) is None


async def test_recall_maps_memory_payload_and_respects_top_k(dependencies) -> None:
    memory, documents = dependencies
    backend = HindsightBackend(
        memory, documents, extractor=FakeExtractor(), recall_mode="fast"
    )

    result = await backend.recall(RecallRequest(query="what changed?", top_k=1))

    assert memory.mode == "fast"
    assert result.chunks[0].chunk_text == "original source chunk"
    assert result.chunks[0].reranker_score == 0.8
    assert result.related_entities[0]["name"] == "TKB"
    assert result.related_docs == [{"id": "doc-1", "title": "week.md"}]


async def test_graph_mapping_and_neighbor_filter(dependencies) -> None:
    memory, documents = dependencies
    backend = HindsightBackend(memory, documents, extractor=FakeExtractor())

    graph = await backend.get_graph()
    assert [node.name for node in graph.nodes] == ["TKB", "Hindsight"]
    assert graph.links[0].source == "TKB"
    assert graph.links[0].target == "Hindsight"

    neighbors = await backend.get_neighbors("TKB")
    assert {node.name for node in neighbors.nodes} == {"TKB", "Hindsight"}


async def test_failed_retain_is_visible_on_document(dependencies) -> None:
    memory, documents = dependencies

    async def fail(**kwargs):
        raise RuntimeError("LLM unavailable")

    memory.retain_document = fail
    backend = HindsightBackend(memory, documents, extractor=FakeExtractor())

    result = await backend.ingest(IngestSource(name="week.md", data=b"content"))

    assert result.status == "failed"
    assert result.error_msg == "LLM unavailable"
