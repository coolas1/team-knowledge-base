"""Engine module contract: the KnowledgeBase Protocol + shared types.

This is THE contract every engine implementation must satisfy. Adapters
(cli.py) and consumers (plugin skills/MCP server, host BFF) program
against these types, never against a concrete backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from src.engine.scope import MemoryScope as MemoryScope
from src.engine.scope import TagExpression as TagExpression
from src.engine.scope import TagFilter as TagFilter
from src.engine.scope import TagGroup as TagGroup


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
    memory_types: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    tags_match: str = "any"
    reference_time: datetime | None = None
    min_scores: dict[str, float] = field(default_factory=dict)
    prefer_observations: bool = False
    include: tuple[str, ...] = ("chunks", "entities")
    include_stale: bool = False
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_candidates: int | None = None


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
    correlation_id: str | None = None
    memory_types: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    tags_match: str = "any"
    reference_time: datetime | None = None
    min_scores: dict[str, float] = field(default_factory=dict)
    prefer_observations: bool = False
    include: tuple[str, ...] = ("chunks", "entities")
    include_stale: bool = False
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_candidates: int | None = None


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


@dataclass(frozen=True, slots=True)
class MemoryExpansionRequest:
    memory_id: str
    include: tuple[str, ...] = ("chunk", "document", "source_facts")
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class MemoryExpansionResult:
    memory: dict
    chunk: dict | None = None
    document: dict | None = None
    source_facts: tuple[dict, ...] = ()
    token_count: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class MentalModelDefinition:
    id: str
    name: str
    source_query: str
    description: str = ""
    tags: tuple[str, ...] = ()
    refresh_mode: Literal["full", "delta"] = "full"
    refresh_after_consolidation: bool = False
    refresh_interval_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class MentalModelRecord:
    id: str
    name: str
    description: str
    source_query: str
    tags: tuple[str, ...]
    summary: str
    version: int
    refresh_mode: str
    refresh_after_consolidation: bool
    refresh_interval_seconds: int | None
    next_refresh_at: datetime | None
    last_success_at: datetime | None
    freshness: str
    error_msg: str | None
    evidence_watermark: int
    source_memory_ids: tuple[str, ...]
    source_versions: dict[str, int]


@dataclass(frozen=True, slots=True)
class ConversationMemoryRecallRequest:
    query: str
    top_k: int = 5
    mode: Literal["fast", "deep"] = "fast"
    memory_types: tuple[str, ...] = ()
    include_source_time: bool = False


@dataclass(frozen=True, slots=True)
class ConversationMemoryItem:
    memory_id: str
    text: str
    memory_type: str
    document_id: str
    session_id: str
    turn_id: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)
    mentioned_at: str | None = None
    occurred_start: str | None = None
    occurred_end: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationMemoryRecallResult:
    memories: list[ConversationMemoryItem] = field(default_factory=list)
    trace: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    session_id: str
    turn_id: str
    user_text: str
    assistant_text: str
    source_timestamp: str | None = None
    reference_timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class ConversationEnqueueResult:
    document_id: str
    status: str
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationForgetRequest:
    session_id: str


@dataclass(frozen=True, slots=True)
class ConversationForgetResult:
    session_id: str
    cancelled_jobs: int = 0
    deleted_documents: int = 0


@dataclass(frozen=True, slots=True)
class ConversationMemoryDiagnostics:
    enabled: bool
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


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
    async def ingest_batch(self, sources: list[IngestSource]) -> list[DocumentRef]: ...
    async def edit_content(self, doc_id: str, content: str) -> DocumentRef: ...
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

    async def expand_memory(
        self, request: MemoryExpansionRequest
    ) -> MemoryExpansionResult | None: ...

    async def create_mental_model(
        self, definition: MentalModelDefinition
    ) -> MentalModelRecord: ...

    async def get_mental_model(self, model_id: str) -> MentalModelRecord | None: ...

    async def list_mental_models(self) -> list[MentalModelRecord]: ...

    async def update_mental_model(
        self, model_id: str, definition: MentalModelDefinition
    ) -> MentalModelRecord: ...

    async def delete_mental_model(self, model_id: str) -> bool: ...

    async def refresh_mental_model(self, model_id: str) -> bool: ...


class ConversationMemory(Protocol):
    """Optional internal capability for automatic conversation memory."""

    async def recall_conversation_memory(
        self, request: ConversationMemoryRecallRequest
    ) -> ConversationMemoryRecallResult: ...

    async def enqueue_conversation_turn(
        self, turn: ConversationTurn
    ) -> ConversationEnqueueResult: ...

    async def forget_conversation_memory(
        self, request: ConversationForgetRequest
    ) -> ConversationForgetResult: ...

    async def conversation_memory_diagnostics(
        self,
    ) -> ConversationMemoryDiagnostics: ...
