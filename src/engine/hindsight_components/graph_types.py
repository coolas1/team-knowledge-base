"""Persistence-neutral DTOs for projecting Hindsight memory into Neo4j."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryGraphDocument:
    id: str
    title: str
    file_type: str
    overview: str = ""


@dataclass(frozen=True, slots=True)
class MemoryGraphMemory:
    id: str
    document_id: str
    memory_type: str
    text: str
    context: str = ""
    chunk_index: int = 0
    occurred_start: str | None = None
    occurred_end: str | None = None
    confidence: float = 1.0
    source_memory_ids: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryGraphEntity:
    id: str
    canonical_name: str
    normalized_name: str
    entity_type: str = "Entity"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryGraphMention:
    memory_id: str
    entity_id: str
    role: str = "mention"


@dataclass(frozen=True, slots=True)
class MemoryGraphLink:
    source_memory_id: str
    target_memory_id: str
    link_type: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryGraphProjection:
    document: MemoryGraphDocument
    memories: tuple[MemoryGraphMemory, ...] = ()
    entities: tuple[MemoryGraphEntity, ...] = ()
    mentions: tuple[MemoryGraphMention, ...] = ()
    links: tuple[MemoryGraphLink, ...] = ()
