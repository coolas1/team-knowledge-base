"""Shared test doubles for the three-module refactor.

FakeKnowledgeBase implements src.engine.interface.KnowledgeBase with in-memory
state so engine CLI/MCP adapters, the BFF, and agent skills can be tested with
no external services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import pytest

from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    IngestSource,
    RecallRequest,
    RecallResult,
)


def pytest_collection_modifyitems(items):
    """Keep live-service tests opt-in even when the full suite is executed."""
    enabled = os.getenv("RUN_INTEGRATION", "").casefold() in {"1", "true", "yes"}
    if enabled:
        return
    marker = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run live tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(marker)


def _host_service_url(url: str) -> str:
    """Translate a Compose-only Ollama hostname for host-side pytest."""
    parsed = urlsplit(url)
    if parsed.hostname != "ollama":
        return url
    port = parsed.port or 11434
    return urlunsplit(
        (parsed.scheme or "http", f"localhost:{port}", parsed.path, "", "")
    )


@pytest.fixture
def integration_host_config(monkeypatch):
    """Make host-run integration tests use host-reachable service URLs."""
    from config.settings import settings
    from src.engine.components.embedder import embedder

    ollama_url = os.getenv("INTEGRATION_OLLAMA_BASE_URL") or _host_service_url(
        settings.ollama_base_url
    )
    llm_url = _host_service_url(settings.llm_base_url)
    monkeypatch.setattr(settings, "ollama_base_url", ollama_url)
    monkeypatch.setattr(settings, "llm_base_url", llm_url)
    monkeypatch.setattr(embedder, "_base_url", ollama_url.rstrip("/"))
    return {"ollama_base_url": ollama_url, "llm_base_url": llm_url}


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
            raise ValueError(f"文档不存在: {doc_id}")
        self.docs[doc_id].status = "pending"
        return self.docs[doc_id]

    async def edit_content(self, doc_id: str, content: str) -> DocumentRef:
        if doc_id not in self.docs:
            raise ValueError(f"文档不存在: {doc_id}")
        self.raw[doc_id] = content.encode()
        self.docs[doc_id].status = "pending"
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
        return {
            "total": len(items),
            "page": page,
            "page_size": page_size,
            "items": items,
        }

    async def get_document(self, doc_id: str) -> dict | None:
        ref = self.docs.get(doc_id)
        if ref is None:
            return None
        out = ref.__dict__
        raw = self.raw.get(doc_id, b"")
        out["raw_text"] = (
            raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else ""
        )
        return out
