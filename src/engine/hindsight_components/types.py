"""Persistence-neutral types shared by retain, recall, and reflect."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ExtractedFact:
    text: str
    fact_type: str = "world"
    entities: list[str] = field(default_factory=list)
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    location: str | None = None
    caused_by: list[int] = field(default_factory=list)
    confidence: float = 1.0


@dataclass(slots=True)
class MemoryDraft:
    id: str
    document_id: str
    chunk_index: int
    memory_index: int
    memory_type: str
    text: str
    source_text: str
    context: str
    embedding: list[float]
    entities: list[str] = field(default_factory=list)
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    confidence: float = 1.0
    is_source_chunk: bool = False
    location: str | None = None
    source_memory_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryLinkDraft:
    source_memory_id: str
    target_memory_id: str
    link_type: str
    weight: float = 1.0


@dataclass(slots=True)
class RetainPlan:
    document_id: str
    title: str
    file_type: str
    source_type: str
    memories: list[MemoryDraft]
    links: list[MemoryLinkDraft]


@dataclass(frozen=True, slots=True)
class RetainResult:
    document_id: str
    chunks: int
    facts: int
    observations: int
    memories: int
    links: int


@dataclass(slots=True)
class RecallCandidate:
    id: str
    document_id: str
    title: str
    text: str
    source_text: str
    chunk_index: int
    memory_type: str = "world"
    context: str = ""
    occurred_start: str | None = None
    occurred_end: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source_memory_ids: list[str] = field(default_factory=list)
    embedding: list[float] | None = None
    semantic_score: float | None = None
    keyword_score: float | None = None
    graph_score: float | None = None
    temporal_score: float | None = None
    reranker_score: float | None = None
    final_score: float = 0.0
    source_ranks: dict[str, int] = field(default_factory=dict)

    def as_evidence(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.memory_type,
            "document_id": self.document_id,
            "chunk_id": f"{self.document_id}_{self.chunk_index}",
            "context": self.context,
            "metadata": {**self.metadata, "title": self.title},
            "occurred_start": self.occurred_start,
            "occurred_end": self.occurred_end,
            "source_memory_ids": list(self.source_memory_ids),
            "scores": {
                "final": self.final_score,
                "reranker": self.reranker_score,
                "semantic": self.semantic_score,
                "keyword": self.keyword_score,
                "graph": self.graph_score,
                "temporal": self.temporal_score,
            },
        }


@dataclass(slots=True)
class RecallResult:
    results: list[RecallCandidate]
    chunks: dict[str, dict[str, Any]]
    entities: dict[str, Any]
    trace: dict[str, Any]

    def evidence(self) -> list[dict[str, Any]]:
        return [item.as_evidence() for item in self.results]


@dataclass(slots=True)
class MentalModel:
    id: str
    name: str
    description: str
    summary: str = ""
    is_directive: bool = False
    trigger: str | None = None
    embedding: list[float] | None = None
    source_memory_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MemoryProfile:
    background: str = ""
    skepticism: int = 3
    literalism: int = 3
    empathy: int = 3


@dataclass(slots=True)
class ReflectionContext:
    mental_models: list[MentalModel] = field(default_factory=list)
    profile: MemoryProfile = field(default_factory=MemoryProfile)


@dataclass(slots=True)
class ReflectResult:
    text: str
    based_on: dict[str, list[dict[str, Any]]]
    tool_trace: list[dict[str, Any]]
