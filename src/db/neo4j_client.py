from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from neo4j import AsyncGraphDatabase

from src.db.config import settings


def _safe_cypher_identifier(value: str, fallback: str) -> str:
    """Only allow identifiers that are safe to interpolate into Cypher."""
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value or fallback)
    if not normalized or normalized[0].isdigit():
        normalized = f"_{normalized}"
    return normalized


@dataclass
class EntityData:
    name: str
    entity_type: str
    description: str = ""


@dataclass
class EntitySource:
    """实体溯源信息。"""
    doc_id: str
    chunk_index: int
    doc_title: str
    team_id: str = "default"


@dataclass
class RelationData:
    from_name: str
    to_name: str
    relation_type: str
    description: str = ""


@dataclass
class GraphQueryResult:
    name: str
    entity_type: str
    properties: dict = field(default_factory=dict)
    relations: list[dict] = field(default_factory=list)


class Neo4jClient:
    """Neo4j 异步客户端，管理知识图谱的实体和关系。"""

    def __init__(self) -> None:
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    async def close(self) -> None:
        await self._driver.close()

    async def initialize(self, default_team_id: str = "default") -> None:
        """Adopt legacy graph data and install tenant-safe uniqueness constraints."""
        async with self._driver.session() as session:
            await session.run(
                "MATCH (n) WHERE n.team_id IS NULL SET n.team_id = $team_id",
                team_id=default_team_id,
            )
            await session.run(
                "MATCH (d:Document) WHERE d.projection_version IS NULL SET d.projection_version = 1"
            )
            await session.run("MATCH (n) WHERE NOT n:Document SET n:Entity")
            await session.run(
                """
                CREATE CONSTRAINT tkb_document_team_id IF NOT EXISTS
                FOR (d:Document) REQUIRE (d.team_id, d.doc_id) IS UNIQUE
                """
            )
            await session.run(
                """
                CREATE CONSTRAINT tkb_entity_team_name IF NOT EXISTS
                FOR (e:Entity) REQUIRE (e.team_id, e.name) IS UNIQUE
                """
            )

    # ── Document 节点 ────────────────────────────────────────────

    async def upsert_document_node(
        self,
        doc_id: str,
        title: str,
        file_type: str,
        overview: str = "",
        team_id: str = "default",
        projection_version: int = 1,
    ) -> None:
        """创建/更新 Document 节点。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (d:Document {team_id: $team_id, doc_id: $doc_id})
                SET d.title = $title,
                    d.file_type = $file_type,
                    d.overview = $overview,
                    d.projection_version = $projection_version
                """,
                doc_id=doc_id,
                title=title,
                file_type=file_type,
                overview=overview,
                team_id=team_id,
                projection_version=projection_version,
            )

    async def delete_document_graph(self, doc_id: str, team_id: str = "default") -> None:
        """删除文档的图谱数据：清理实体 sources + 删 Document 节点。"""
        async with self._driver.session() as session:
            # 1. 从关系 sources 中移除文档来源，并删除失去全部来源的关系。
            relation_result = await session.run(
                """
                MATCH (a {team_id: $team_id})-[r]->(b {team_id: $team_id})
                WHERE r.sources IS NOT NULL AND r.sources CONTAINS $doc_id
                RETURN elementId(r) AS relation_id, r.sources AS sources
                """,
                team_id=team_id,
                doc_id=doc_id,
            )
            for record in await relation_result.data():
                sources: list[dict] = json.loads(record["sources"])
                new_sources = [source for source in sources if source["doc_id"] != doc_id]
                if new_sources:
                    await session.run(
                        """
                        MATCH ()-[r]->() WHERE elementId(r) = $relation_id
                        SET r.sources = $sources
                        """,
                        relation_id=record["relation_id"],
                        sources=json.dumps(new_sources, ensure_ascii=False),
                    )
                else:
                    await session.run(
                        "MATCH ()-[r]->() WHERE elementId(r) = $relation_id DELETE r",
                        relation_id=record["relation_id"],
                    )

            # 2. 从所有实体的 sources 中移除该 doc_id 的条目
            result = await session.run(
                """
                MATCH (e {team_id: $team_id})
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                RETURN e.name AS name, e.sources AS sources
                """,
                doc_id=doc_id,
                team_id=team_id,
            )
            records = await result.data()
            for record in records:
                sources: list[dict] = json.loads(record["sources"])
                new_sources = [s for s in sources if s["doc_id"] != doc_id]
                await session.run(
                    """
                    MATCH (e {team_id: $team_id, name: $name})
                    SET e.sources = $sources
                    """,
                    name=record["name"],
                    team_id=team_id,
                    sources=json.dumps(new_sources, ensure_ascii=False) if new_sources else "[]",
                )

            # 3. 删除 sources 为空的孤立实体
            await session.run(
                """
                MATCH (e {team_id: $team_id})
                WHERE e.sources = '[]' OR e.sources = ''
                DETACH DELETE e
                """,
                team_id=team_id,
            )

            # 4. 删除 Document 节点及其 doc 级关系
            await session.run(
                """
                MATCH (d:Document {team_id: $team_id, doc_id: $doc_id})
                OPTIONAL MATCH (d)-[r]-()
                DELETE r, d
                """,
                doc_id=doc_id,
                team_id=team_id,
            )

    async def ensure_document_node(
        self, doc_id: str, title: str, team_id: str = "default"
    ) -> None:
        """Ensure a relation target exists without downgrading an existing projection."""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (d:Document {team_id: $team_id, doc_id: $doc_id})
                ON CREATE SET d.title = $title, d.file_type = 'unknown',
                              d.overview = '', d.projection_version = 0
                """,
                team_id=team_id,
                doc_id=doc_id,
                title=title,
            )

    # ── 实体 ────────────────────────────────────────────────────

    async def upsert_entity(
        self, entity: EntityData, source: EntitySource
    ) -> None:
        """创建/更新实体节点，追加溯源来源。

        MERGE by name → 同名实体全局唯一。
        sources 存储为 JSON 字符串列表（Neo4j 不支持 list<map>）。
        去重逻辑在 Python 层处理。
        """
        new_source = {
            "doc_id": source.doc_id,
            "chunk_index": source.chunk_index,
            "doc_title": source.doc_title,
        }
        entity_label = _safe_cypher_identifier(entity.entity_type, "Entity")

        async with self._driver.session() as session:
            # 1. MERGE 实体节点，保留较长的 description
            await session.run(
                f"""
                MERGE (e:Entity {{team_id: $team_id, name: $name}})
                SET e:{entity_label}
                SET e.entity_type = $entity_type,
                    e.description = CASE
                        WHEN size($desc) > size(coalesce(e.description, ''))
                        THEN $desc
                        ELSE e.description
                    END
                """,
                name=entity.name,
                entity_type=entity.entity_type,
                desc=entity.description,
                team_id=source.team_id,
            )

            # 2. 读取现有 sources，追加去重
            result = await session.run(
                """
                MATCH (e {team_id: $team_id, name: $name})
                RETURN e.sources AS sources
                """,
                name=entity.name,
                team_id=source.team_id,
            )
            record = await result.single()
            sources_json = record["sources"] if record and record["sources"] else "[]"
            sources: list[dict] = json.loads(sources_json)

            # 去重检查
            is_dup = any(
                s["doc_id"] == new_source["doc_id"]
                and s["chunk_index"] == new_source["chunk_index"]
                for s in sources
            )
            if not is_dup:
                sources.append(new_source)
                await session.run(
                    """
                    MATCH (e {team_id: $team_id, name: $name})
                    SET e.sources = $sources
                    """,
                    name=entity.name,
                    team_id=source.team_id,
                    sources=json.dumps(sources, ensure_ascii=False),
                )

    # ── 关系 ────────────────────────────────────────────────────

    async def upsert_relation(
        self, relation: RelationData, source: EntitySource
    ) -> None:
        """创建/更新实体间关系，带溯源。"""
        new_source = {
            "doc_id": source.doc_id,
            "chunk_index": source.chunk_index,
        }
        relation_type = _safe_cypher_identifier(relation.relation_type, "RELATED_TO")

        async with self._driver.session() as session:
            # 1. MERGE 关系，保留较长的 description
            await session.run(
                f"""
                MATCH (a {{team_id: $team_id, name: $from_name}})
                MATCH (b {{team_id: $team_id, name: $to_name}})
                MERGE (a)-[r:{relation_type}]->(b)
                SET r.description = CASE
                        WHEN size($desc) > size(coalesce(r.description, ''))
                        THEN $desc
                        ELSE r.description
                    END
                """,
                from_name=relation.from_name,
                to_name=relation.to_name,
                desc=relation.description,
                team_id=source.team_id,
            )

            # 2. 追加 sources 去重
            result = await session.run(
                f"""
                MATCH (a {{team_id: $team_id, name: $from_name}})-[r:{relation_type}]->(b {{team_id: $team_id, name: $to_name}})
                RETURN r.sources AS sources
                """,
                from_name=relation.from_name,
                to_name=relation.to_name,
                team_id=source.team_id,
            )
            record = await result.single()
            if record:
                sources_json = record["sources"] if record["sources"] else "[]"
                sources: list[dict] = json.loads(sources_json)
                is_dup = any(
                    s["doc_id"] == new_source["doc_id"]
                    and s["chunk_index"] == new_source["chunk_index"]
                    for s in sources
                )
                if not is_dup:
                    sources.append(new_source)
                    await session.run(
                        f"""
                        MATCH (a {{team_id: $team_id, name: $from_name}})-[r:{relation_type}]->(b {{team_id: $team_id, name: $to_name}})
                        SET r.sources = $sources
                        """,
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        team_id=source.team_id,
                        sources=json.dumps(sources, ensure_ascii=False),
                    )

    async def create_doc_relation(
        self,
        source_doc_id: str,
        target_doc_id: str,
        relation_type: str,
        reason: str,
        team_id: str = "default",
    ) -> None:
        """创建 Document↔Document 显式关联（file_relations）。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (d1:Document {team_id: $team_id, doc_id: $source_doc_id})
                MATCH (d2:Document {team_id: $team_id, doc_id: $target_doc_id})
                MERGE (d1)-[r:RELATED_TO {relation_type: $relation_type}]->(d2)
                SET r.reason = $reason
                """,
                source_doc_id=source_doc_id,
                target_doc_id=target_doc_id,
                relation_type=relation_type,
                reason=reason,
                team_id=team_id,
            )

    # ── 查询 ────────────────────────────────────────────────────

    async def query_neighbors(
        self, name: str, hops: int = 2, team_id: str = "default"
    ) -> list[GraphQueryResult]:
        """获取实体 N 跳内的所有邻居。"""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (start {{team_id: $team_id, name: $name}})
                MATCH (start)-[*1..{hops}]-(neighbor)
                WHERE neighbor <> start AND neighbor.team_id = $team_id
                RETURN DISTINCT neighbor, labels(neighbor) AS labels
                """,
                name=name,
                team_id=team_id,
            )
            records = await result.data()

            results: list[GraphQueryResult] = []
            for record in records:
                node = record["neighbor"]
                labels = record["labels"]
                # 过滤掉 Neo4j 内部 label
                entity_type = node.get("entity_type") or next(
                    (label for label in labels if label not in ("Document", "Entity")), "Unknown"
                )
                results.append(
                    GraphQueryResult(
                        name=node.get("name", ""),
                        entity_type=entity_type,
                        properties=dict(node),
                    )
                )
            return results

    async def get_entity_details(self, name: str, team_id: str = "default") -> GraphQueryResult | None:
        """查询实体详情 + 直接关联关系。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {team_id: $team_id, name: $name})
                OPTIONAL MATCH (n)-[r]-(other)
                WHERE other IS NULL OR other.team_id = $team_id
                RETURN n, labels(n) AS labels,
                       collect({
                           type: type(r),
                           direction: CASE WHEN startNode(r) = n THEN 'OUT' ELSE 'IN' END,
                           other_name: other.name,
                           other_labels: labels(other),
                           description: r.description
                       }) AS relations
                """,
                name=name,
                team_id=team_id,
            )
            record = await result.single()
            if not record:
                return None

            node = record["n"]
            labels = record["labels"]
            entity_type = node.get("entity_type") or next(
                (label for label in labels if label not in ("Document", "Entity")), "Unknown"
            )

            return GraphQueryResult(
                name=node.get("name", ""),
                entity_type=entity_type,
                properties=dict(node),
                relations=[r for r in record["relations"] if r.get("type")],
            )

    async def get_document_entities(self, doc_id: str, team_id: str = "default") -> list[GraphQueryResult]:
        """获取某文档关联的所有实体（通过 sources 属性）。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e {team_id: $team_id})
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                RETURN e, labels(e) AS labels
                """,
                doc_id=doc_id,
                team_id=team_id,
            )
            records = await result.data()
            results = []
            for record in records:
                node = record["e"]
                labels = record["labels"]
                entity_type = node.get("entity_type") or next(
                    (label for label in labels if label not in ("Document", "Entity")), "Entity"
                )
                results.append(
                    GraphQueryResult(
                        name=node.get("name", ""),
                        entity_type=entity_type,
                        properties=dict(node),
                    )
                )
            return results

    async def find_entities_by_source(
        self, doc_id: str, chunk_index: int, team_id: str = "default"
    ) -> list[GraphQueryResult]:
        """查找 sources 包含指定 (doc_id, chunk_index) 的实体。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e {team_id: $team_id})
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id_fragment
                RETURN e, labels(e) AS labels
                """,
                doc_id_fragment=doc_id,
                team_id=team_id,
            )
            records = await result.data()
            results = []
            for record in records:
                node = record["e"]
                labels = record["labels"]
                sources: list[dict] = json.loads(node.get("sources", "[]"))
                # 精确匹配 chunk_index
                if any(
                    s["doc_id"] == doc_id and s["chunk_index"] == chunk_index
                    for s in sources
                ):
                    entity_type = node.get("entity_type") or next(
                        (label for label in labels if label not in ("Document", "Entity")), "Entity"
                    )
                    results.append(
                        GraphQueryResult(
                            name=node.get("name", ""),
                            entity_type=entity_type,
                            properties=dict(node),
                        )
                    )
            return results

    async def get_full_graph(self, team_id: str = "default") -> dict:
        """返回全图数据：所有实体节点 + 所有实体间关系。"""
        async with self._driver.session() as session:
            # 1. 查询所有实体节点（排除 Document）
            node_result = await session.run(
                """
                MATCH (e)
                WHERE NOT e:Document AND e.sources IS NOT NULL AND e.team_id = $team_id
                RETURN e.name AS name, e.description AS description,
                       e.entity_type AS entity_type,
                       e.sources AS sources, labels(e) AS labels
                """,
                team_id=team_id,
            )
            node_records = await node_result.data()
            nodes = []
            for r in node_records:
                labels = r["labels"]
                entity_type = r["entity_type"] or next(
                    (label for label in labels if label not in ("Document", "Entity")), "Unknown"
                )
                sources_raw = r["sources"] or "[]"
                nodes.append({
                    "name": r["name"],
                    "type": entity_type,
                    "description": r["description"] or "",
                    "sources": json.loads(sources_raw),
                })

            # 2. 查询所有实体间关系（排除 Document 节点和 RELATED_TO）
            link_result = await session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE NOT a:Document AND NOT b:Document
                  AND type(r) <> 'RELATED_TO'
                  AND a.team_id = $team_id AND b.team_id = $team_id
                RETURN a.name AS source, b.name AS target,
                       type(r) AS type, r.description AS description
                """,
                team_id=team_id,
            )
            link_records = await link_result.data()
            links = [
                {
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"],
                    "description": r["description"] or "",
                }
                for r in link_records
            ]

            return {"nodes": nodes, "links": links}

    async def get_related_docs(self, doc_ids: list[str], team_id: str = "default") -> list[dict]:
        """从指定文档出发，通过 Document↔Document 边查找关联文档。"""
        if not doc_ids:
            return []

        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (d1:Document)-[r:RELATED_TO]-(d2:Document)
                WHERE d1.team_id = $team_id AND d2.team_id = $team_id
                  AND d1.doc_id IN $doc_ids AND NOT d2.doc_id IN $doc_ids
                RETURN DISTINCT d2.doc_id AS doc_id,
                       d2.title AS title,
                       type(r) AS rel_type,
                       r.relation_type AS relation_type,
                       r.reason AS reason
                """,
                doc_ids=doc_ids,
                team_id=team_id,
            )
            records = await result.data()
            return [
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "relation_type": r.get("relation_type", ""),
                    "reason": r.get("reason", ""),
                }
                for r in records
            ]

    async def get_document_projection_version(
        self, doc_id: str, team_id: str = "default"
    ) -> int | None:
        """返回 Neo4j 文档投影版本，供 PostgreSQL 对账。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (d:Document {team_id: $team_id, doc_id: $doc_id})
                RETURN d.projection_version AS projection_version
                """,
                team_id=team_id,
                doc_id=doc_id,
            )
            record = await result.single()
            if not record or record["projection_version"] is None:
                return None
            return int(record["projection_version"])
