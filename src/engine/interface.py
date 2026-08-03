"""Engine module contract: the KnowledgeBase Protocol + shared types.

This is THE contract every engine implementation must satisfy. Adapters
(cli.py, mcp.py) and consumers (agent engine_client, frontend BFF) program
against these types, never against a concrete backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


class NotSupported(Exception):
    """Raised by an optional KnowledgeBase method the backend does not support."""


@dataclass
class Capabilities:
    """Declares what a backend supports. Optional methods raise NotSupported."""

    graph: bool = False
    partial_update: bool = False
    multimodal: bool = False
    namespace: bool = False


@dataclass
class IngestSource:
    """A file to ingest: either raw bytes (name+data) or a path on disk."""

    name: str
    data: bytes = b""
    path: Path | None = None


@dataclass
class DocumentRef:
    id: str
    title: str
    file_type: str
    status: str
    overview: str = ""
    error_msg: str | None = None
    memory_status: str | None = None
    memory_error_msg: str | None = None
    memory_count: int = 0
    memory_link_count: int = 0


@dataclass
class RecallRequest:
    query: str
    top_k: int = 20
    mode: Literal["auto", "fast", "deep"] = "auto"
    needs_answer: bool = False


@dataclass
class RecallChunk:
    doc_id: str
    title: str
    chunk_text: str
    reranker_score: float
    vector_score: float
    memory_id: str | None = None
    memory_type: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RecallResult:
    chunks: list[RecallChunk] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)
    answer: str | None = None
    mode_used: Literal["fast", "deep"] | None = None
    strategy_used: Literal["recall", "reflect"] | None = None
    based_on: dict[str, list[dict]] = field(default_factory=dict)
    trace: dict = field(default_factory=dict)


@dataclass
class KnowledgeQueryRequest:
    """Unified high-level query without team/bank partitioning."""

    query: str
    strategy: Literal["auto", "recall", "reflect"] = "auto"
    mode: Literal["fast", "deep"] = "deep"
    top_k: int = 10
    needs_answer: bool = True


@dataclass
class KnowledgeSource:
    memory_id: str
    memory_type: str
    doc_id: str
    title: str
    chunk_text: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class KnowledgeQueryResult:
    strategy_used: Literal["recall", "reflect"]
    answer: str | None = None
    sources: list[KnowledgeSource] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    based_on: dict[str, list[dict]] = field(default_factory=dict)
    trace: dict = field(default_factory=dict)


@dataclass
class GraphNode:
    name: str
    type: str
    description: str = ""
    sources: list[dict] = field(default_factory=list)


@dataclass
class GraphLink:
    source: str
    target: str
    type: str
    description: str = ""


@dataclass
class GraphData:
    nodes: list[GraphNode] = field(default_factory=list)
    links: list[GraphLink] = field(default_factory=list)


class KnowledgeBase(Protocol):
    """Stable engine contract. Engine = no agents; LLM only for embeddings,
    chunk summaries (overview), and graph entity/relation extraction."""

    capabilities: Capabilities

    async def ingest(self, source: IngestSource) -> DocumentRef: ...
    async def reingest(self, doc_id: str) -> DocumentRef: ...
    async def remove(self, doc_id: str) -> None: ...
    async def recall(self, request: RecallRequest) -> RecallResult: ...
    async def get_graph(self, entity: str | None = None) -> GraphData: ...
    async def get_neighbors(self, entity: str) -> GraphData: ...
    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict: ...
    async def get_document(self, doc_id: str) -> dict | None: ...


class DocumentIndexHook(Protocol):
    """Optional sidecar index lifecycle extension.

    Hooks must not own file extraction or the Document lifecycle. Implementors
    receive text only after the primary GraphRAG index succeeds.
    """

    async def after_indexed(
        self,
        *,
        document_id: str,
        title: str,
        content: str,
        file_type: str,
    ) -> None: ...

    async def before_remove(self, document_id: str) -> None: ...


class KnowledgeQuery(Protocol):
    """Optional high-level recall/reflect query capability."""

    async def query(self, request: KnowledgeQueryRequest) -> KnowledgeQueryResult: ...
