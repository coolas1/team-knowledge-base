"""Validated document-level projection of PostgreSQL memory state to Neo4j."""

from __future__ import annotations

from typing import Protocol

from .graph_types import MemoryGraphProjection

MEMORY_LINK_RELATIONSHIPS = {
    "caused_by": "CAUSED_BY",
    "semantic": "SEMANTIC",
    "temporal": "TEMPORAL",
    "entity": "ENTITY_RELATED",
    "evidence": "EVIDENCE",
}


class MemoryGraphStore(Protocol):
    async def ensure_schema(self) -> None: ...

    async def replace_document(self, projection: MemoryGraphProjection) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...


class MemoryGraphProjector:
    """Validate projection boundaries before invoking a graph store."""

    def __init__(self, store: MemoryGraphStore) -> None:
        self._store = store

    async def ensure_schema(self) -> None:
        await self._store.ensure_schema()

    async def replace_document(self, projection: MemoryGraphProjection) -> None:
        self._validate(projection)
        await self._store.replace_document(projection)

    async def delete_document(self, document_id: str) -> None:
        if not document_id.strip():
            raise ValueError("document_id cannot be empty")
        await self._store.delete_document(document_id)

    @staticmethod
    def _validate(projection: MemoryGraphProjection) -> None:
        document_id = projection.document.id.strip()
        if not document_id:
            raise ValueError("document id cannot be empty")

        memory_ids: set[str] = set()
        for memory in projection.memories:
            if not memory.id.strip():
                raise ValueError("memory id cannot be empty")
            if memory.document_id != document_id:
                raise ValueError(
                    f"memory {memory.id} belongs to {memory.document_id}, "
                    f"expected {document_id}"
                )
            if memory.id in memory_ids:
                raise ValueError(f"duplicate memory id: {memory.id}")
            memory_ids.add(memory.id)

        entity_ids: set[str] = set()
        normalized_names: set[str] = set()
        for entity in projection.entities:
            if not entity.id.strip() or not entity.normalized_name.strip():
                raise ValueError("entity id and normalized_name cannot be empty")
            if entity.id in entity_ids:
                raise ValueError(f"duplicate entity id: {entity.id}")
            if entity.normalized_name in normalized_names:
                raise ValueError(
                    f"duplicate normalized entity: {entity.normalized_name}"
                )
            entity_ids.add(entity.id)
            normalized_names.add(entity.normalized_name)

        mentions: set[tuple[str, str, str]] = set()
        for mention in projection.mentions:
            if mention.memory_id not in memory_ids:
                raise ValueError(
                    f"mention references unknown memory: {mention.memory_id}"
                )
            if mention.entity_id not in entity_ids:
                raise ValueError(
                    f"mention references unknown entity: {mention.entity_id}"
                )
            key = (mention.memory_id, mention.entity_id, mention.role)
            if key in mentions:
                raise ValueError(f"duplicate memory entity mention: {key}")
            mentions.add(key)

        links: set[tuple[str, str, str]] = set()
        for link in projection.links:
            if link.link_type not in MEMORY_LINK_RELATIONSHIPS:
                raise ValueError(f"unsupported memory link type: {link.link_type}")
            if link.source_memory_id not in memory_ids:
                raise ValueError(
                    f"link references unknown source memory: {link.source_memory_id}"
                )
            if not link.target_memory_id.strip():
                raise ValueError("link target memory cannot be empty")
            key = (
                link.source_memory_id,
                link.target_memory_id,
                link.link_type,
            )
            if key in links:
                raise ValueError(f"duplicate memory link: {key}")
            links.add(key)
