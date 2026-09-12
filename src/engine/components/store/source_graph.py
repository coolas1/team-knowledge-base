"""Source-preserving GraphRAG projection with authoritative document visibility.

Entity descriptions are aggregated only within one document at write time.
Read-time aggregation runs after PostgreSQL has selected permitted sources.
Legacy aggregated descriptions require *all* their sources to remain visible.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict

from sqlalchemy import select

from src.engine.scope import MemoryScope
from .models import Document, public_document_filter
from .neo4j import (
    Neo4jClient,
    EntityData,
    EntitySource,
    RelationData,
    GraphQueryResult,
    _group_entity_items,
    _group_relation_items,
    _quote_cypher_identifier,
)


class SourceNeo4jClient(Neo4jClient):
    def __init__(self, *, driver=None, session_factory=None, scope=None):
        if driver is None:
            super().__init__()
        else:
            self._driver = driver
        if session_factory is None:
            from .postgres import async_session_factory

            session_factory = async_session_factory
        self._sessions = session_factory
        self.scope = scope or MemoryScope()

    def with_scope(self, scope: MemoryScope) -> SourceNeo4jClient:
        return SourceNeo4jClient(
            driver=self._driver, session_factory=self._sessions, scope=scope
        )

    async def _owner(self, doc_id: str):
        async with self._sessions() as session:
            doc = await session.get(Document, uuid.UUID(doc_id))
            if doc is None:
                raise ValueError("document does not exist")
            return doc.bank_id, list(doc.tags or [])

    async def _visible_ids(self) -> list[str]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(Document.id).where(public_document_filter(self.scope))
            )
            return [str(value) for value in rows]

    async def ensure_schema(self):
        async with self._driver.session() as session:
            await session.run(
                "CREATE CONSTRAINT tkb_source_entity_key IF NOT EXISTS FOR (e:TKBSourceEntity) REQUIRE e.source_key IS UNIQUE"
            )
            # Add native source IDs to old JSON projections without guessing which
            # source supplied an aggregated description. Interrupted runs resume.
            for pattern, target in (("(e)", "e"), ("()-[e]->()", "e")):
                result = await session.run(
                    f"MATCH {pattern} WHERE e.sources IS NOT NULL AND e.source_doc_ids IS NULL RETURN elementId(e) AS id, e.sources AS sources"
                )
                for row in await result.data():
                    sources = json.loads(row["sources"] or "[]")
                    ids = sorted({s["doc_id"] for s in sources if s.get("doc_id")})
                    await session.run(
                        f"MATCH {pattern} WHERE elementId({target}) = $id SET e.source_doc_ids = $ids, e.bank_id = coalesce(e.bank_id, 'default-team')",
                        id=row["id"],
                        ids=ids,
                    )

    async def upsert_document_node(
        self,
        doc_id,
        title,
        file_type,
        overview="",
        version_number=1,
        is_current=True,
    ):
        bank, tags = await self._owner(doc_id)
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (d:Document {doc_id: $id})
                SET d.bank_id=$bank, d.tags=$tags, d.title=$title,
                    d.file_type=$file_type, d.overview=$overview,
                    d.version_number=$version_number, d.is_current=$is_current
                """,
                id=doc_id,
                bank=bank,
                tags=tags,
                title=title,
                file_type=file_type,
                overview=overview,
                version_number=version_number,
                is_current=is_current,
            )

    async def upsert_entity(self, entity: EntityData, source: EntitySource):
        await self.upsert_entities_batch([(entity, source)])

    async def upsert_relation(self, relation: RelationData, source: EntitySource):
        await self.upsert_relations_batch([(relation, source)])

    async def upsert_entities_batch(self, items):
        if items:
            await self.ensure_schema()
        by_doc = defaultdict(list)
        for entity, source in items:
            by_doc[source.doc_id].append((entity, source))
        for doc_id, group in by_doc.items():
            bank, tags = await self._owner(doc_id)
            rows = []
            for entity, sources in _group_entity_items(group):
                key = json.dumps(
                    [bank, doc_id, entity.entity_type, entity.name], ensure_ascii=False
                )
                rows.append(
                    dict(
                        key=key,
                        name=entity.name,
                        entity_type=entity.entity_type,
                        description=entity.description,
                        sources=json.dumps(sources, ensure_ascii=False),
                    )
                )
            async with self._driver.session() as session:
                await session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (e:TKBSourceEntity {source_key: row.key})
                    SET e.bank_id=$bank, e.doc_id=$doc_id, e.source_doc_ids=[$doc_id],
                        e.tags=$tags, e.name=row.name, e.entity_type=row.entity_type,
                        e.description=row.description, e.sources=row.sources
                """,
                    rows=rows,
                    bank=bank,
                    doc_id=doc_id,
                    tags=tags,
                )

    async def upsert_relations_batch(self, items):
        by_doc = defaultdict(list)
        for relation, source in items:
            by_doc[source.doc_id].append((relation, source))
        for doc_id, group in by_doc.items():
            bank, _tags = await self._owner(doc_id)
            by_type = defaultdict(list)
            for relation, sources in _group_relation_items(group):
                by_type[relation.relation_type].append(
                    dict(
                        source=relation.from_name,
                        target=relation.to_name,
                        description=relation.description,
                        sources=json.dumps(sources),
                    )
                )
            for rel_type, rows in by_type.items():
                label = _quote_cypher_identifier(rel_type, "RELATED_TO")
                async with self._driver.session() as session:
                    await session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (a:TKBSourceEntity {{bank_id:$bank, doc_id:$doc_id, name:row.source}})
                        MATCH (b:TKBSourceEntity {{bank_id:$bank, doc_id:$doc_id, name:row.target}})
                        MERGE (a)-[r:{label}]->(b)
                        SET r.bank_id=$bank, r.source_doc_ids=[$doc_id],
                            r.description=row.description, r.sources=row.sources
                    """,
                        rows=rows,
                        bank=bank,
                        doc_id=doc_id,
                    )

    async def create_doc_relation(
        self, source_doc_id, target_doc_id, relation_type, reason
    ):
        bank, tags = await self._owner(source_doc_id)
        target_bank, target_tags = await self._owner(target_doc_id)
        # A relation reason can describe both endpoints. Require identical write
        # visibility as well as bank, so it cannot bridge private source buckets.
        if bank != target_bank or set(tags) != set(target_tags):
            return
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (a:Document {doc_id:$source}), (b:Document {doc_id:$target})
                WHERE a.bank_id=$bank AND b.bank_id=$bank
                MERGE (a)-[r:RELATED_TO {relation_type:$relation_type}]->(b)
                SET r.reason=$reason, r.bank_id=$bank
            """,
                source=source_doc_id,
                target=target_doc_id,
                bank=bank,
                relation_type=relation_type,
                reason=reason,
            )

    async def delete_document_graph(self, doc_id):
        # UUID is authorized by the backend before primary document deletion.
        # Legacy aggregates retain missing source IDs and become unreadable;
        # stripping an ID would falsely attribute its description to survivors.
        async with self._driver.session() as session:
            await self.ensure_schema()
            await session.run(
                "MATCH (e) WHERE NOT e:TKBSourceEntity AND $id IN e.source_doc_ids SET e.projection_stale=true",
                id=doc_id,
            )
            await session.run(
                "MATCH ()-[r]->() WHERE $id IN r.source_doc_ids SET r.projection_stale=true",
                id=doc_id,
            )
            await session.run(
                "MATCH (e:TKBSourceEntity {doc_id:$id}) DETACH DELETE e", id=doc_id
            )
            await session.run(
                "MATCH (d:Document {doc_id:$id}) DETACH DELETE d", id=doc_id
            )

    async def get_full_graph(self):
        await self.ensure_schema()
        ids = await self._visible_ids()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e) WHERE NOT e:Document AND e.bank_id=$bank
                  AND NOT coalesce(e.projection_stale, false)
                  AND size(e.source_doc_ids)>0
                  AND all(id IN e.source_doc_ids WHERE id IN $ids)
                RETURN e.name AS name, coalesce(e.entity_type, head(labels(e))) AS type,
                       e.description AS description, e.sources AS sources
            """,
                bank=self.scope.bank_id,
                ids=ids,
            )
            raw_nodes = await result.data()
            result = await session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE NOT a:Document AND NOT b:Document
                  AND a.bank_id=$bank AND b.bank_id=$bank AND r.bank_id=$bank
                  AND NOT coalesce(a.projection_stale, false)
                  AND NOT coalesce(b.projection_stale, false)
                  AND NOT coalesce(r.projection_stale, false)
                  AND size(a.source_doc_ids)>0 AND size(b.source_doc_ids)>0 AND size(r.source_doc_ids)>0
                  AND all(id IN a.source_doc_ids WHERE id IN $ids)
                  AND all(id IN b.source_doc_ids WHERE id IN $ids)
                  AND all(id IN r.source_doc_ids WHERE id IN $ids)
                RETURN a.name AS source, b.name AS target, type(r) AS type, r.description AS description
            """,
                bank=self.scope.bank_id,
                ids=ids,
            )
            raw_links = await result.data()
        nodes = {}
        for row in raw_nodes:
            key = (row["name"], row["type"])
            node = nodes.setdefault(
                key,
                dict(name=row["name"], type=row["type"], description="", sources=[]),
            )
            if len(row["description"] or "") > len(node["description"]):
                node["description"] = row["description"]
            for source in json.loads(row["sources"] or "[]"):
                if source not in node["sources"]:
                    node["sources"].append(source)
        links = {}
        for row in raw_links:
            key = (row["source"], row["target"], row["type"])
            if key not in links or len(row["description"] or "") > len(
                links[key]["description"]
            ):
                links[key] = {**row, "description": row["description"] or ""}
        return dict(nodes=list(nodes.values()), links=list(links.values()))

    async def get_entity_details(self, name):
        graph = await self.get_full_graph()
        nodes = sorted(
            (n for n in graph["nodes"] if n["name"] == name), key=lambda n: n["type"]
        )
        if not nodes:
            return None
        relations = [
            dict(
                type=r["type"],
                direction="OUT" if r["source"] == name else "IN",
                other_name=r["target"] if r["source"] == name else r["source"],
                description=r["description"],
            )
            for r in graph["links"]
            if name in (r["source"], r["target"])
        ]
        return GraphQueryResult(
            name, nodes[0]["type"], properties=nodes[0], relations=relations
        )

    async def get_document_entities(self, doc_id):
        graph = await self.get_full_graph()
        return [
            GraphQueryResult(n["name"], n["type"], properties=n)
            for n in graph["nodes"]
            if any(s["doc_id"] == doc_id for s in n["sources"])
        ]

    async def find_entities_by_source(self, doc_id, chunk_index):
        return [
            e
            for e in await self.get_document_entities(doc_id)
            if any(
                s["doc_id"] == doc_id and s["chunk_index"] == chunk_index
                for s in e.properties["sources"]
            )
        ]

    async def query_neighbors(self, name, hops=2):
        if not isinstance(hops, int) or not 1 <= hops <= 8:
            raise ValueError("hops must be between 1 and 8")
        graph = await self.get_full_graph()
        seen = {name}
        frontier = {name}
        for _ in range(hops):
            adjacent = {r["target"] for r in graph["links"] if r["source"] in frontier}
            adjacent |= {r["source"] for r in graph["links"] if r["target"] in frontier}
            frontier = adjacent - seen
            seen |= frontier
        return [
            GraphQueryResult(n["name"], n["type"], properties=n)
            for n in graph["nodes"]
            if n["name"] != name and n["name"] in seen
        ]

    async def get_related_docs(self, doc_ids):
        ids = await self._visible_ids()
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Document)-[r:RELATED_TO]-(b:Document)
                WHERE a.doc_id IN $seeds AND a.doc_id IN $ids AND b.doc_id IN $ids
                  AND NOT b.doc_id IN $seeds
                  AND coalesce(a.bank_id, 'default-team')=$bank
                  AND coalesce(b.bank_id, 'default-team')=$bank
                  AND coalesce(r.bank_id, 'default-team')=$bank
                RETURN DISTINCT b.doc_id AS doc_id, b.title AS title,
                       r.relation_type AS relation_type, r.reason AS reason
            """,
                seeds=doc_ids,
                ids=ids,
                bank=self.scope.bank_id,
            )
            return await result.data()
