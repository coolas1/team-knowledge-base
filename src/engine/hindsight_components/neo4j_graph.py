"""Neo4j graph store for Hindsight Memory/Entity/Link projections."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from neo4j import AsyncGraphDatabase

from config.settings import settings

from .graph_projector import MEMORY_LINK_RELATIONSHIPS
from .graph_types import MemoryGraphProjection

_SCHEMA_QUERIES = (
    """
    CREATE CONSTRAINT hindsight_memory_id IF NOT EXISTS
    FOR (memory:HindsightMemory) REQUIRE memory.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT hindsight_entity_id IF NOT EXISTS
    FOR (entity:HindsightEntity) REQUIRE entity.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT hindsight_entity_normalized_name IF NOT EXISTS
    FOR (entity:HindsightEntity) REQUIRE entity.normalized_name IS UNIQUE
    """,
    """
    CREATE CONSTRAINT tkb_document_id IF NOT EXISTS
    FOR (document:Document) REQUIRE document.doc_id IS UNIQUE
    """,
    """
    CREATE INDEX hindsight_memory_document IF NOT EXISTS
    FOR (memory:HindsightMemory) ON (memory.document_id)
    """,
    """
    CREATE INDEX hindsight_memory_type IF NOT EXISTS
    FOR (memory:HindsightMemory) ON (memory.memory_type)
    """,
)

_UPSERT_DOCUMENT = """
MERGE (document:Document {doc_id: $document_id})
SET document.title = $title,
    document.file_type = $file_type,
    document.overview = $overview
"""

_DELETE_DOCUMENT_MEMORIES = """
MATCH (memory:HindsightMemory {document_id: $document_id})
DETACH DELETE memory
"""

_UPSERT_MEMORIES = """
MATCH (document:Document {doc_id: $document_id})
UNWIND $memories AS item
MERGE (memory:HindsightMemory {id: item.id})
SET memory.document_id = $document_id,
    memory.memory_type = item.memory_type,
    memory.text = item.text,
    memory.context = item.context,
    memory.chunk_index = item.chunk_index,
    memory.occurred_start = item.occurred_start,
    memory.occurred_end = item.occurred_end,
    memory.confidence = item.confidence,
    memory.source_memory_ids = item.source_memory_ids,
    memory.tags = item.tags,
    memory.metadata_json = item.metadata_json,
    memory.placeholder = false
MERGE (document)-[:CONTAINS_MEMORY]->(memory)
"""

_UPSERT_ENTITIES = """
UNWIND $entities AS item
MERGE (entity:HindsightEntity {normalized_name: item.normalized_name})
SET entity.id = item.id,
    entity.canonical_name = item.canonical_name,
    entity.entity_type = item.entity_type,
    entity.metadata_json = item.metadata_json
"""

_UPSERT_MENTIONS = """
UNWIND $mentions AS item
MATCH (memory:HindsightMemory {id: item.memory_id})
MATCH (entity:HindsightEntity {id: item.entity_id})
MERGE (memory)-[mention:MENTIONS]->(entity)
SET mention.role = item.role
"""

_DELETE_ORPHANS = """
MATCH (entity:HindsightEntity)
WHERE NOT (entity)<-[:MENTIONS]-(:HindsightMemory)
DETACH DELETE entity
"""

_DELETE_ORPHAN_PLACEHOLDERS = """
MATCH (memory:HindsightMemory {placeholder: true})
WHERE NOT (memory)--()
DETACH DELETE memory
"""


class HindsightNeo4jGraphStore:
    """Idempotent, document-level Neo4j projection store.

    PostgreSQL remains authoritative.  This class only owns nodes carrying the
    ``HindsightMemory``/``HindsightEntity`` labels and their relationships.
    """

    def __init__(self, driver: Any | None = None) -> None:
        self._driver = driver or AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    async def close(self) -> None:
        await self._driver.close()

    async def ensure_schema(self) -> None:
        async with self._driver.session() as session:
            for query in _SCHEMA_QUERIES:
                await session.run(query)

    async def replace_document(self, projection: MemoryGraphProjection) -> None:
        async with self._driver.session() as session:
            await session.execute_write(self._replace_document, projection)

    async def delete_document(self, document_id: str) -> None:
        async with self._driver.session() as session:
            await session.execute_write(self._delete_document, document_id)

    @staticmethod
    async def _replace_document(tx: Any, projection: MemoryGraphProjection) -> None:
        document = projection.document
        await tx.run(
            _UPSERT_DOCUMENT,
            document_id=document.id,
            title=document.title,
            file_type=document.file_type,
            overview=document.overview,
        )
        await tx.run(_DELETE_DOCUMENT_MEMORIES, document_id=document.id)
        await tx.run(
            _UPSERT_MEMORIES,
            document_id=document.id,
            memories=[
                {
                    "id": memory.id,
                    "memory_type": memory.memory_type,
                    "text": memory.text,
                    "context": memory.context,
                    "chunk_index": memory.chunk_index,
                    "occurred_start": memory.occurred_start,
                    "occurred_end": memory.occurred_end,
                    "confidence": memory.confidence,
                    "source_memory_ids": list(memory.source_memory_ids),
                    "tags": list(memory.tags),
                    "metadata_json": json.dumps(
                        memory.metadata, ensure_ascii=False, sort_keys=True
                    ),
                }
                for memory in projection.memories
            ],
        )
        await tx.run(
            _UPSERT_ENTITIES,
            entities=[
                {
                    "id": entity.id,
                    "canonical_name": entity.canonical_name,
                    "normalized_name": entity.normalized_name,
                    "entity_type": entity.entity_type,
                    "metadata_json": json.dumps(
                        entity.metadata, ensure_ascii=False, sort_keys=True
                    ),
                }
                for entity in projection.entities
            ],
        )
        await tx.run(
            _UPSERT_MENTIONS,
            mentions=[
                {
                    "memory_id": mention.memory_id,
                    "entity_id": mention.entity_id,
                    "role": mention.role,
                }
                for mention in projection.mentions
            ],
        )

        links_by_type: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for link in projection.links:
            links_by_type[link.link_type].append(
                {
                    "source_memory_id": link.source_memory_id,
                    "target_memory_id": link.target_memory_id,
                    "weight": link.weight,
                    "metadata_json": json.dumps(
                        link.metadata, ensure_ascii=False, sort_keys=True
                    ),
                }
            )
        for link_type, links in links_by_type.items():
            relationship = MEMORY_LINK_RELATIONSHIPS[link_type]
            await tx.run(
                HindsightNeo4jGraphStore._link_query(relationship), links=links
            )

        await tx.run(_DELETE_ORPHANS)
        await tx.run(_DELETE_ORPHAN_PLACEHOLDERS)

    @staticmethod
    async def _delete_document(tx: Any, document_id: str) -> None:
        await tx.run(_DELETE_DOCUMENT_MEMORIES, document_id=document_id)
        await tx.run(_DELETE_ORPHANS)
        await tx.run(_DELETE_ORPHAN_PLACEHOLDERS)

    @staticmethod
    def _link_query(relationship: str) -> str:
        if relationship not in MEMORY_LINK_RELATIONSHIPS.values():
            raise ValueError(f"unsupported Neo4j relationship: {relationship}")
        return f"""
        UNWIND $links AS item
        MATCH (source:HindsightMemory {{id: item.source_memory_id}})
        MERGE (target:HindsightMemory {{id: item.target_memory_id}})
        ON CREATE SET target.placeholder = true
        MERGE (source)-[relation:{relationship}]->(target)
        SET relation.weight = item.weight,
            relation.metadata_json = item.metadata_json
        """
