"""Shared test doubles for the three-module refactor.

FakeKnowledgeBase implements src.engine.interface.KnowledgeBase with in-memory
state so engine CLI/MCP adapters, the BFF, and agent skills can be tested with
no external services.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    GraphLink,
    GraphNode,
    IngestSource,
    NotSupported,
    RecallChunk,
    RecallRequest,
    RecallResult,
)


@dataclass
class FakeKnowledgeBase:
    capabilities: Capabilities = field(default_factory=Capabilities)
    docs: dict[str, DocumentRef] = field(default_factory=dict)
    raw: dict[str, bytes] = field(default_factory=dict)
    graph: GraphData = field(default_factory=GraphData)
    recall_calls: list[str] = field(default_factory=list)

    async def ingest(self, source: IngestSource) -> DocumentRef:
        import uuid

        doc_id = str(uuid.uuid4())
        ref = DocumentRef(
            id=doc_id, title=source.name, file_type="markdown", status="indexed"
        )
        self.docs[doc_id] = ref
        self.raw[doc_id] = source.data
        return ref

    async def reingest(self, doc_id: str) -> DocumentRef:
        if doc_id not in self.docs:
            raise KeyError(doc_id)
        self.docs[doc_id].status = "indexed"
        return self.docs[doc_id]

    async def remove(self, doc_id: str) -> None:
        self.docs.pop(doc_id, None)
        self.raw.pop(doc_id, None)

    async def recall(self, request: RecallRequest) -> RecallResult:
        self.recall_calls.append(request.query)
        return RecallResult(chunks=[], related_entities=[], related_docs=[])

    async def get_graph(self, entity: str | None = None) -> GraphData:
        return self.graph

    async def get_neighbors(self, entity: str) -> GraphData:
        return self.graph

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        items = list(self.docs.values())
        return {"total": len(items), "page": page, "page_size": page_size, "items": items}

    async def get_document(self, doc_id: str) -> dict | None:
        ref = self.docs.get(doc_id)
        if ref is None:
            return None
        out = ref.__dict__
        raw = self.raw.get(doc_id, b"")
        out["raw_text"] = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else ""
        return out
