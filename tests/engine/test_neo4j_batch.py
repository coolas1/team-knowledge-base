"""Batched graph upserts: python-side grouping + UNWIND query shape.

The grouping helpers are pure; the client methods are exercised through a
fake driver that records queries so the UNWIND shape is asserted without a
live Neo4j (parity with the single-item methods is covered by the
integration tests).
"""
import json

from src.engine.components.store import neo4j as neo4j_mod
from src.engine.components.store.neo4j import (
    EntityData,
    EntitySource,
    Neo4jClient,
    RelationData,
)


def _src(chunk_index):
    return EntitySource(doc_id="d1", chunk_index=chunk_index, doc_title="t.md")


def test_group_entity_items_dedups_and_keeps_longest_description():
    items = [
        (EntityData("Acme", "Organization", "short"), _src(0)),
        (EntityData("Acme", "Organization", "a much longer description"), _src(2)),
        (EntityData("Acme", "Organization", "short"), _src(0)),  # 重复 source 去重
        (EntityData("Acme", "Person", "same name other type"), _src(1)),
    ]

    grouped = neo4j_mod._group_entity_items(items)

    by_key = {(e.name, e.entity_type): s for e, s in grouped}
    assert set(by_key) == {("Acme", "Organization"), ("Acme", "Person")}
    org_sources = by_key[("Acme", "Organization")]
    assert [s["chunk_index"] for s in org_sources] == [0, 2]
    org_entity = next(e for e, _ in grouped if e.entity_type == "Organization")
    assert org_entity.description == "a much longer description"


def test_group_relation_items_dedups_by_triple():
    items = [
        (RelationData("Acme", "Bob", "EMPLOYS", "v1"), _src(0)),
        (RelationData("Acme", "Bob", "EMPLOYS", "version one long"), _src(1)),
        (RelationData("Acme", "Bob", "PARTNERS_WITH", "x"), _src(1)),
    ]

    grouped = neo4j_mod._group_relation_items(items)

    by_key = {
        (r.from_name, r.to_name, r.relation_type): s for r, s in grouped
    }
    assert set(by_key) == {
        ("Acme", "Bob", "EMPLOYS"),
        ("Acme", "Bob", "PARTNERS_WITH"),
    }
    employs = by_key[("Acme", "Bob", "EMPLOYS")]
    assert [s["chunk_index"] for s in employs] == [0, 1]
    employs_rel = next(
        r for r, _ in grouped if r.relation_type == "EMPLOYS"
    )
    assert employs_rel.description == "version one long"


class _FakeResult:
    def __init__(self, records):
        self._records = records

    async def data(self):
        return self._records


class _FakeSession:
    """Records run() calls; answers the MERGE query with canned sources."""

    def __init__(self, existing_sources="[]"):
        self.queries = []
        self.existing_sources = existing_sources

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query, **params):
        self.queries.append((query, params))
        if "MERGE" not in query:
            return _FakeResult([])
        if "row.from_name" in query:  # relation MERGE
            return _FakeResult(
                [
                    {
                        "from_name": r["from_name"],
                        "to_name": r["to_name"],
                        "sources": self.existing_sources,
                    }
                    for r in params["rows"]
                ]
            )
        return _FakeResult(
            [
                {"name": r["name"], "sources": self.existing_sources}
                for r in params["rows"]
            ]
        )


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def _client_with(fake_session):
    client = Neo4jClient.__new__(Neo4jClient)  # 跳过 __init__，不连真实服务
    client._driver = _FakeDriver(fake_session)
    return client


async def test_upsert_entities_batch_one_unwind_per_type():
    session = _FakeSession(existing_sources="[]")
    client = _client_with(session)

    await client.upsert_entities_batch(
        [
            (EntityData("Acme", "Organization", "d1"), _src(0)),
            (EntityData("Zed", "Organization", "d2"), _src(1)),
            (EntityData("Bob", "Person", "d3"), _src(0)),
        ]
    )

    merges = [q for q, _ in session.queries if "MERGE" in q]
    assert len(merges) == 2  # 每个 entity_type 一次 UNWIND
    assert any("`Organization`" in q for q in merges)
    assert any("`Person`" in q for q in merges)

    source_sets = [p for q, p in session.queries if "SET e.sources" in q]
    assert len(source_sets) == 2  # 现有 sources 为空，全部追加
    org_updates = next(
        p for p in source_sets
        if any(u["name"] == "Acme" for u in p["updates"])
    )
    acme_update = next(u for u in org_updates["updates"] if u["name"] == "Acme")
    assert json.loads(acme_update["sources"])[0]["chunk_index"] == 0


async def test_upsert_entities_batch_skips_sources_already_present():
    existing = json.dumps(
        [{"doc_id": "d1", "chunk_index": 0, "doc_title": "t.md"}]
    )
    session = _FakeSession(existing_sources=existing)
    client = _client_with(session)

    await client.upsert_entities_batch(
        [(EntityData("Acme", "Organization", "d"), _src(0))]
    )

    assert [q for q, _ in session.queries if "SET e.sources" in q] == []


async def test_upsert_relations_batch_one_unwind_per_type():
    session = _FakeSession(existing_sources="[]")
    client = _client_with(session)

    await client.upsert_relations_batch(
        [
            (RelationData("Acme", "Bob", "EMPLOYS", "d1"), _src(0)),
            (RelationData("Acme", "Cid", "EMPLOYS", "d2"), _src(1)),
            (RelationData("Acme", "Dee", "PARTNERS_WITH", "d3"), _src(0)),
        ]
    )

    merges = [q for q, _ in session.queries if "MERGE (a)-[r:" in q]
    assert len(merges) == 2
    assert any("`EMPLOYS`" in q for q in merges)
    assert any("`PARTNERS_WITH`" in q for q in merges)

    source_sets = [p for q, p in session.queries if "SET r.sources" in q]
    assert len(source_sets) == 2


async def test_batch_methods_short_circuit_on_empty():
    session = _FakeSession()
    client = _client_with(session)

    await client.upsert_entities_batch([])
    await client.upsert_relations_batch([])

    assert session.queries == []
