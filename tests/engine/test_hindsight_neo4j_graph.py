from __future__ import annotations

from dataclasses import replace

import pytest

from src.engine.hindsight_components.graph_projector import (
    MEMORY_LINK_RELATIONSHIPS,
    MemoryGraphProjector,
)
from src.engine.hindsight_components.graph_types import (
    MemoryGraphDocument,
    MemoryGraphEntity,
    MemoryGraphLink,
    MemoryGraphMemory,
    MemoryGraphMention,
    MemoryGraphProjection,
)
from src.engine.hindsight_components.neo4j_graph import HindsightNeo4jGraphStore


def projection() -> MemoryGraphProjection:
    return MemoryGraphProjection(
        document=MemoryGraphDocument(
            id="document-1",
            title="week.md",
            file_type="markdown",
            overview="Weekly progress",
        ),
        memories=(
            MemoryGraphMemory(
                id="memory-1",
                document_id="document-1",
                memory_type="world",
                text="TKB uses Hindsight.",
                context="weekly report",
                chunk_index=1,
                confidence=0.9,
                tags=("tkb",),
                metadata={"语言": "中文"},
            ),
            MemoryGraphMemory(
                id="memory-2",
                document_id="document-1",
                memory_type="observation",
                text="The original interfaces remain compatible.",
            ),
        ),
        entities=(
            MemoryGraphEntity(
                id="entity-1",
                canonical_name="TKB",
                normalized_name="tkb",
                entity_type="Project",
            ),
        ),
        mentions=(MemoryGraphMention(memory_id="memory-1", entity_id="entity-1"),),
        links=tuple(
            MemoryGraphLink(
                source_memory_id="memory-1",
                target_memory_id=(
                    "external-memory" if link_type == "semantic" else "memory-2"
                ),
                link_type=link_type,
                weight=0.8,
                metadata={"source": "retain"},
            )
            for link_type in MEMORY_LINK_RELATIONSHIPS
        ),
    )


class FakeTransaction:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls

    async def run(self, query: str, **parameters):
        self.calls.append((query, parameters))


class FakeSession:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def run(self, query: str, **parameters):
        self.calls.append((query, parameters))

    async def execute_write(self, callback, *args):
        return await callback(FakeTransaction(self.calls), *args)


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def session(self):
        return FakeSession(self.calls)

    async def close(self):
        self.closed = True


class FakeStore:
    def __init__(self) -> None:
        self.schema_calls = 0
        self.projections: list[MemoryGraphProjection] = []
        self.deleted: list[str] = []

    async def ensure_schema(self) -> None:
        self.schema_calls += 1

    async def replace_document(self, value: MemoryGraphProjection) -> None:
        self.projections.append(value)

    async def delete_document(self, document_id: str) -> None:
        self.deleted.append(document_id)


async def test_projector_validates_and_forwards_projection():
    store = FakeStore()
    projector = MemoryGraphProjector(store)
    value = projection()

    await projector.ensure_schema()
    await projector.replace_document(value)
    await projector.delete_document("document-1")

    assert store.schema_calls == 1
    assert store.projections == [value]
    assert store.deleted == ["document-1"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            replace(
                projection(),
                memories=(
                    replace(projection().memories[0], document_id="other-document"),
                ),
            ),
            "belongs to",
        ),
        (
            replace(
                projection(),
                mentions=(
                    MemoryGraphMention(
                        memory_id="missing-memory", entity_id="entity-1"
                    ),
                ),
            ),
            "unknown memory",
        ),
        (
            replace(
                projection(),
                links=(
                    MemoryGraphLink(
                        source_memory_id="memory-1",
                        target_memory_id="memory-2",
                        link_type="invented",
                    ),
                ),
            ),
            "unsupported memory link type",
        ),
    ],
)
async def test_projector_rejects_invalid_projection(value, message):
    store = FakeStore()
    with pytest.raises(ValueError, match=message):
        await MemoryGraphProjector(store).replace_document(value)
    assert store.projections == []


async def test_neo4j_store_creates_constraints_and_indexes():
    driver = FakeDriver()
    store = HindsightNeo4jGraphStore(driver)

    await store.ensure_schema()

    queries = "\n".join(query for query, _ in driver.calls)
    assert "hindsight_memory_id" in queries
    assert "hindsight_entity_normalized_name" in queries
    assert "tkb_document_id" in queries
    assert "hindsight_memory_document" in queries


async def test_neo4j_store_projects_all_memory_link_categories():
    driver = FakeDriver()
    store = HindsightNeo4jGraphStore(driver)

    await store.replace_document(projection())

    queries = "\n".join(query for query, _ in driver.calls)
    assert "MERGE (document:Document" in queries
    assert "MERGE (memory:HindsightMemory" in queries
    assert "MERGE (entity:HindsightEntity" in queries
    assert "MERGE (memory)-[mention:MENTIONS]" in queries
    assert "CONTAINS_MEMORY" in queries
    for relationship in MEMORY_LINK_RELATIONSHIPS.values():
        assert f"[relation:{relationship}]" in queries

    memory_call = next(
        parameters for query, parameters in driver.calls if "CONTAINS_MEMORY" in query
    )
    assert memory_call["memories"][0]["metadata_json"] == '{"语言": "中文"}'

    semantic_call = next(
        parameters
        for query, parameters in driver.calls
        if "[relation:SEMANTIC]" in query
    )
    assert semantic_call["links"][0]["target_memory_id"] == "external-memory"


async def test_neo4j_replace_is_idempotent_by_cypher_contract():
    driver = FakeDriver()
    store = HindsightNeo4jGraphStore(driver)
    value = projection()

    await store.replace_document(value)
    first_calls = list(driver.calls)
    driver.calls.clear()
    await store.replace_document(value)

    assert driver.calls == first_calls
    assert all("CREATE (" not in query for query, _ in driver.calls)
    assert any("MERGE (source)-[relation:" in query for query, _ in driver.calls)


async def test_neo4j_delete_only_removes_hindsight_projection():
    driver = FakeDriver()
    store = HindsightNeo4jGraphStore(driver)

    await store.delete_document("document-1")

    queries = "\n".join(query for query, _ in driver.calls)
    assert "MATCH (memory:HindsightMemory" in queries
    assert "DETACH DELETE memory" in queries
    assert "DELETE document" not in queries
    assert driver.calls[0][1] == {"document_id": "document-1"}


def test_relationship_query_rejects_dynamic_cypher_type():
    with pytest.raises(ValueError, match="unsupported Neo4j relationship"):
        HindsightNeo4jGraphStore._link_query("DROP_ALL")
