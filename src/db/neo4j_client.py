from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from neo4j import AsyncGraphDatabase

from src.db.config import settings


def _sanitize_label(name: str) -> str:
    """将实体/关系类型转为合法的 Neo4j 标签。

    LLM 可能返回含空格/标点的类型（如 "Site Name"），直接拼入
    Cypher 会导致语法错误。这里保留字母/数字/下划线/中文，
    其余字符替换为下划线，并确保不以数字开头。
    """
    if not name:
        return "Unknown"
    s = re.sub(r"[^\w\u4e00-\u9fff]", "_", name, flags=re.UNICODE)
    s = s.strip("_")
    if not s:
        return "Unknown"
    if s[0].isdigit():
        s = "T_" + s
    return s


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

    # ── Document 节点 ────────────────────────────────────────────

    async def upsert_document_node(
        self,
        doc_id: str,
        title: str,
        file_type: str,
        overview: str = "",
    ) -> None:
        """创建/更新 Document 节点。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (d:Document {doc_id: $doc_id})
                SET d.title = $title,
                    d.file_type = $file_type,
                    d.overview = $overview
                """,
                doc_id=doc_id,
                title=title,
                file_type=file_type,
                overview=overview,
            )

    async def delete_document_graph(self, doc_id: str) -> None:
        """删除文档的图谱数据：清理实体 sources + 删 Document 节点。"""
        async with self._driver.session() as session:
            # 1. 从所有实体的 sources 中移除该 doc_id 的条目
            result = await session.run(
                """
                MATCH (e)
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                RETURN e.name AS name, e.sources AS sources
                """,
                doc_id=doc_id,
            )
            records = await result.data()
            for record in records:
                sources: list[dict] = json.loads(record["sources"])
                new_sources = [s for s in sources if s["doc_id"] != doc_id]
                await session.run(
                    """
                    MATCH (e {name: $name})
                    SET e.sources = $sources
                    """,
                    name=record["name"],
                    sources=json.dumps(new_sources, ensure_ascii=False) if new_sources else "[]",
                )

            # 2. 删除 sources 为空的孤立实体
            await session.run(
                """
                MATCH (e)
                WHERE e.sources = '[]' OR e.sources = ''
                DETACH DELETE e
                """
            )

            # 3. 删除 Document 节点及其 doc 级关系
            await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})
                OPTIONAL MATCH (d)-[r]-()
                DELETE r, d
                """,
                doc_id=doc_id,
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
        label = _sanitize_label(entity.entity_type)

        async with self._driver.session() as session:
            # 1. MERGE 实体节点，保留较长的 description
            await session.run(
                f"""
                MERGE (e:{label} {{name: $name}})
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
            )

            # 2. 读取现有 sources，追加去重
            result = await session.run(
                """
                MATCH (e {name: $name})
                RETURN e.sources AS sources
                """,
                name=entity.name,
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
                    MATCH (e {name: $name})
                    SET e.sources = $sources
                    """,
                    name=entity.name,
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
        rel_type = _sanitize_label(relation.relation_type)

        async with self._driver.session() as session:
            # 1. MERGE 关系，保留较长的 description
            await session.run(
                f"""
                MATCH (a {{name: $from_name}})
                MATCH (b {{name: $to_name}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.description = CASE
                        WHEN size($desc) > size(coalesce(r.description, ''))
                        THEN $desc
                        ELSE r.description
                    END
                """,
                from_name=relation.from_name,
                to_name=relation.to_name,
                desc=relation.description,
            )

            # 2. 追加 sources 去重
            result = await session.run(
                f"""
                MATCH (a {{name: $from_name}})-[r:{rel_type}]->(b {{name: $to_name}})
                RETURN r.sources AS sources
                """,
                from_name=relation.from_name,
                to_name=relation.to_name,
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
                        MATCH (a {{name: $from_name}})-[r:{rel_type}]->(b {{name: $to_name}})
                        SET r.sources = $sources
                        """,
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        sources=json.dumps(sources, ensure_ascii=False),
                    )

    async def create_doc_relation(
        self,
        source_doc_id: str,
        target_doc_id: str,
        relation_type: str,
        reason: str,
    ) -> None:
        """创建 Document↔Document 显式关联（file_relations）。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (d1:Document {doc_id: $source_doc_id})
                MATCH (d2:Document {doc_id: $target_doc_id})
                MERGE (d1)-[r:RELATED_TO {relation_type: $relation_type}]->(d2)
                SET r.reason = $reason
                """,
                source_doc_id=source_doc_id,
                target_doc_id=target_doc_id,
                relation_type=relation_type,
                reason=reason,
            )

    # ── 查询 ────────────────────────────────────────────────────

    async def query_neighbors(
        self, name: str, hops: int = 2
    ) -> list[GraphQueryResult]:
        """获取实体 N 跳内的所有邻居。"""
        async with self._driver.session() as session:
            result = await session.run(
                f"""
                MATCH (start {{name: $name}})
                MATCH (start)-[*1..{hops}]-(neighbor)
                WHERE neighbor <> start
                RETURN DISTINCT neighbor, labels(neighbor) AS labels
                """,
                name=name,
            )
            records = await result.data()

            results: list[GraphQueryResult] = []
            for record in records:
                node = record["neighbor"]
                labels = record["labels"]
                # 过滤掉 Neo4j 内部 label
                entity_type = next(
                    (l for l in labels if l != "Document"), "Unknown"
                )
                results.append(
                    GraphQueryResult(
                        name=node.get("name", ""),
                        entity_type=entity_type,
                        properties=dict(node),
                    )
                )
            return results

    async def get_entity_details(self, name: str) -> GraphQueryResult | None:
        """查询实体详情 + 直接关联关系。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (n {name: $name})
                OPTIONAL MATCH (n)-[r]-(other)
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
            )
            record = await result.single()
            if not record:
                return None

            node = record["n"]
            labels = record["labels"]
            entity_type = next((l for l in labels if l != "Document"), "Unknown")

            return GraphQueryResult(
                name=node.get("name", ""),
                entity_type=entity_type,
                properties=dict(node),
                relations=[r for r in record["relations"] if r.get("type")],
            )

    async def get_document_entities(self, doc_id: str) -> list[GraphQueryResult]:
        """获取某文档关联的所有实体（通过 sources 属性）。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e)
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                RETURN e, labels(e) AS labels
                """,
                doc_id=doc_id,
            )
            records = await result.data()
            results = []
            for record in records:
                node = record["e"]
                labels = record["labels"]
                entity_type = next(
                    (l for l in labels if l not in ("Document",)), "Entity"
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
        self, doc_id: str, chunk_index: int
    ) -> list[GraphQueryResult]:
        """查找 sources 包含指定 (doc_id, chunk_index) 的实体。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e)
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id_fragment
                RETURN e, labels(e) AS labels
                """,
                doc_id_fragment=doc_id,
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
                    entity_type = next(
                        (l for l in labels if l not in ("Document",)), "Entity"
                    )
                    results.append(
                        GraphQueryResult(
                            name=node.get("name", ""),
                            entity_type=entity_type,
                            properties=dict(node),
                        )
                    )
            return results

    async def get_full_graph(self) -> dict:
        """返回全图数据：所有实体节点 + 所有实体间关系。"""
        async with self._driver.session() as session:
            # 1. 查询所有实体节点（排除 Document）
            node_result = await session.run(
                """
                MATCH (e)
                WHERE NOT e:Document AND e.sources IS NOT NULL
                RETURN e.name AS name, e.description AS description,
                       e.sources AS sources, labels(e) AS labels
                """
            )
            node_records = await node_result.data()
            nodes = []
            for r in node_records:
                labels = r["labels"]
                entity_type = next(
                    (l for l in labels if l != "Document"), "Unknown"
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
                RETURN a.name AS source, b.name AS target,
                       type(r) AS type, r.description AS description
                """
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

    async def get_related_docs(self, doc_ids: list[str]) -> list[dict]:
        """从指定文档出发，通过 Document↔Document 边查找关联文档。"""
        if not doc_ids:
            return []

        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (d1:Document)-[r:RELATED_TO]-(d2:Document)
                WHERE d1.doc_id IN $doc_ids AND NOT d2.doc_id IN $doc_ids
                RETURN DISTINCT d2.doc_id AS doc_id,
                       d2.title AS title,
                       type(r) AS rel_type,
                       r.relation_type AS relation_type,
                       r.reason AS reason
                """,
                doc_ids=doc_ids,
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
