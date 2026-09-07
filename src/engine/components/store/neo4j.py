from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import Neo4jError

from config.settings import settings

logger = logging.getLogger(__name__)

# 瞬态错误（死锁/锁超时等）重试参数。
TRANSIENT_RETRY_ATTEMPTS = 3
TRANSIENT_RETRY_BASE_DELAY = 0.5  # 秒，指数退避基数


def _is_transient(error: Exception) -> bool:
    """Neo4j 瞬态错误（死锁、锁等待超时等）可安全重试。"""
    return isinstance(error, Neo4jError) and (
        error.code in ("Neo.TransientError.Transaction.DeadlockDetected",)
        or "deadlock" in str(error).lower()
        or error.code.startswith("Neo.TransientError")
    )


def _quote_cypher_identifier(value: str, fallback: str) -> str:
    identifier = value.strip() or fallback
    return f"`{identifier.replace('`', '``')}`"


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


def _group_entity_items(
    items: list[tuple[EntityData, EntitySource]],
) -> list[tuple[EntityData, list[dict]]]:
    """按 (name, entity_type) 聚合：description 取最长，sources 按出现顺序去重。"""
    grouped: dict[tuple[str, str], list] = {}
    for entity, source in items:
        new_source = {
            "doc_id": source.doc_id,
            "chunk_index": source.chunk_index,
            "doc_title": source.doc_title,
        }
        key = (entity.name, entity.entity_type)
        if key not in grouped:
            grouped[key] = [entity, [new_source]]
            continue
        existing_entity, sources = grouped[key]
        if len(entity.description) > len(existing_entity.description):
            grouped[key][0] = EntityData(
                name=existing_entity.name,
                entity_type=existing_entity.entity_type,
                description=entity.description,
            )
        if new_source not in sources:
            sources.append(new_source)
    return [(entity, sources) for entity, sources in grouped.values()]


def _group_relation_items(
    items: list[tuple[RelationData, EntitySource]],
) -> list[tuple[RelationData, list[dict]]]:
    """按 (from_name, to_name, relation_type) 聚合：description 取最长，sources 去重。"""
    grouped: dict[tuple[str, str, str], list] = {}
    for relation, source in items:
        new_source = {
            "doc_id": source.doc_id,
            "chunk_index": source.chunk_index,
        }
        key = (relation.from_name, relation.to_name, relation.relation_type)
        if key not in grouped:
            grouped[key] = [relation, [new_source]]
            continue
        existing_relation, sources = grouped[key]
        if len(relation.description) > len(existing_relation.description):
            grouped[key][0] = RelationData(
                from_name=existing_relation.from_name,
                to_name=existing_relation.to_name,
                relation_type=existing_relation.relation_type,
                description=relation.description,
            )
        if new_source not in sources:
            sources.append(new_source)
    return [(relation, sources) for relation, sources in grouped.values()]


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
        version_number: int = 1,
        is_current: bool = True,
    ) -> None:
        """创建/更新 Document 节点（含版本链属性）。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (d:Document {doc_id: $doc_id})
                SET d.title = $title,
                    d.file_type = $file_type,
                    d.overview = $overview,
                    d.version_number = $version_number,
                    d.is_current = $is_current
                """,
                doc_id=doc_id,
                title=title,
                file_type=file_type,
                overview=overview,
                version_number=version_number,
                is_current=is_current,
            )

    # ── 版本链投影 ──────────────────────────────────────────────

    async def link_next_version(self, from_doc_id: str, to_doc_id: str) -> None:
        """连接两个版本：(:Document)-[:NEXT_VERSION]->(:Document)。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (prev:Document {doc_id: $from_doc_id})
                MATCH (next:Document {doc_id: $to_doc_id})
                MERGE (prev)-[r:NEXT_VERSION]->(next)
                SET r.created_at = toString(date())
                """,
                from_doc_id=from_doc_id,
                to_doc_id=to_doc_id,
            )

    async def upsert_changes(
        self,
        doc_id: str,
        from_version: int,
        to_version: int,
        summary: str,
        changes: list[dict],
    ) -> None:
        """写入版本 diff：(:Change)-[:CHANGE_OF]->(:Document)。"""
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})
                MERGE (c:Change {doc_id: $doc_id})
                SET c.from_version = $from_version,
                    c.to_version = $to_version,
                    c.summary = $summary,
                    c.changes = $changes
                MERGE (c)-[:CHANGE_OF]->(d)
                """,
                doc_id=doc_id,
                from_version=from_version,
                to_version=to_version,
                summary=summary,
                changes=json.dumps(changes, ensure_ascii=False),
            )

    async def get_version_chain(self, doc_id: str) -> list[dict]:
        """查询某版本所在版本链（沿 NEXT_VERSION 双向展开，按版本号排序）。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})
                MATCH (chain:Document)
                WHERE chain = d
                   OR (chain)-[:NEXT_VERSION*]->(d)
                   OR (d)-[:NEXT_VERSION*]->(chain)
                RETURN chain.doc_id AS doc_id,
                    chain.title AS title,
                    chain.version_number AS version_number,
                    chain.is_current AS is_current,
                    chain.overview AS overview
                ORDER BY chain.version_number
                """,
                doc_id=doc_id,
            )
            records = await result.data()
            return [
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "version_number": r["version_number"],
                    "is_current": r["is_current"],
                    "overview": r["overview"] or "",
                }
                for r in records
            ]

    async def delete_document_graph(self, doc_id: str) -> None:
        """删除文档的图谱数据：清理实体 sources + 删 Document 节点。

        并发删除同一版本链的文档时，两个删除会触碰同一批 MERGE 聚合的
        实体节点。逐条 SET 会在不同事务间交叉加锁引发死锁
        （TransientError.DeadlockDetected），因此：
        1. sources 清理合并为单条 UNWIND 批量写（一个事务一次性加锁）；
        2. 读取按 name 排序，与批处理共同保证确定的加锁顺序；
        3. 整个删除包在瞬态错误重试里（指数退避）。
        """
        for attempt in range(TRANSIENT_RETRY_ATTEMPTS):
            try:
                await self._delete_document_graph_once(doc_id)
                return
            except Neo4jError as error:
                if attempt + 1 >= TRANSIENT_RETRY_ATTEMPTS or not _is_transient(error):
                    raise
                delay = TRANSIENT_RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "删除文档图谱遭遇瞬态错误（第 %s 次），%.1fs 后重试: %s",
                    attempt + 1, delay, error.code,
                )
                await asyncio.sleep(delay)

    async def _delete_document_graph_once(self, doc_id: str) -> None:
        async with self._driver.session() as session:
            # 1. 从所有实体的 sources 中移除该 doc_id 的条目。
            #    ORDER BY name 保证并发删除以相同顺序触碰实体。
            result = await session.run(
                """
                MATCH (e)
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                RETURN e.name AS name, e.sources AS sources
                ORDER BY name
                """,
                doc_id=doc_id,
            )
            records = await result.data()
            if records:
                updates = []
                for record in records:
                    sources: list[dict] = json.loads(record["sources"])
                    new_sources = [s for s in sources if s["doc_id"] != doc_id]
                    updates.append(
                        {
                            "name": record["name"],
                            "sources": (
                                json.dumps(new_sources, ensure_ascii=False)
                                if new_sources
                                else "[]"
                            ),
                        }
                    )
                # 单条批量写：一次事务、一次性加锁，替代原来的逐条 SET。
                await session.run(
                    """
                    UNWIND $updates AS u
                    MATCH (e {name: u.name})
                    SET e.sources = u.sources
                    """,
                    updates=updates,
                )

            # 2. 删除 sources 为空的孤立实体
            await session.run(
                """
                MATCH (e)
                WHERE e.sources = '[]' OR e.sources = ''
                DETACH DELETE e
                """
            )

            # 3. 删除版本 diff Change 节点（版本链上的孤儿子图）
            await session.run(
                """
                MATCH (c:Change {doc_id: $doc_id})
                DETACH DELETE c
                """,
                doc_id=doc_id,
            )

            # 4. 删除 Document 节点及其 doc 级关系
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
        entity_label = _quote_cypher_identifier(entity.entity_type, "Entity")

        async with self._driver.session() as session:
            # 1. MERGE 实体节点，保留较长的 description
            await session.run(
                f"""
                MERGE (e:{entity_label} {{name: $name}})
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
        relation_type = _quote_cypher_identifier(relation.relation_type, "RELATED_TO")

        async with self._driver.session() as session:
            # 1. MERGE 关系，保留较长的 description
            await session.run(
                f"""
                MATCH (a {{name: $from_name}})
                MATCH (b {{name: $to_name}})
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
            )

            # 2. 追加 sources 去重
            result = await session.run(
                f"""
                MATCH (a {{name: $from_name}})-[r:{relation_type}]->(b {{name: $to_name}})
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
                        MATCH (a {{name: $from_name}})-[r:{relation_type}]->(b {{name: $to_name}})
                        SET r.sources = $sources
                        """,
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        sources=json.dumps(sources, ensure_ascii=False),
                    )

    # ── 批量写入 ─────────────────────────────────────────────

    async def upsert_entities_batch(
        self, items: list[tuple[EntityData, EntitySource]]
    ) -> None:
        """批量写入实体：每个 entity_type 一次 UNWIND MERGE + 至多一次 sources 回写。

        语义与逐条 upsert_entity 一致（同 label 同名 MERGE、description 取较长、
        sources 追加去重），但把每个实体 2-3 次往返压缩为每类型 2 次。
        """
        if not items:
            return
        by_type: dict[str, list[tuple[EntityData, list[dict]]]] = {}
        for entity, sources in _group_entity_items(items):
            by_type.setdefault(entity.entity_type, []).append((entity, sources))

        for entity_type, group in by_type.items():
            label = _quote_cypher_identifier(entity_type, "Entity")
            rows = [
                {
                    "name": e.name,
                    "entity_type": e.entity_type,
                    "description": e.description,
                    "sources": json.dumps(s, ensure_ascii=False),
                }
                for e, s in group
            ]
            async with self._driver.session() as session:
                result = await session.run(
                    f"""
                    UNWIND $rows AS row
                    MERGE (e:{label} {{name: row.name}})
                    SET e.entity_type = row.entity_type,
                        e.description = CASE
                            WHEN size(row.description) > size(coalesce(e.description, ''))
                            THEN row.description
                            ELSE e.description
                        END
                    RETURN row.name AS name, e.sources AS sources
                    """,
                    rows=rows,
                )
                records = await result.data()

            existing_by_name = {
                r["name"]: (json.loads(r["sources"]) if r["sources"] else [])
                for r in records
            }
            updates = []
            for row in rows:
                merged = list(existing_by_name.get(row["name"], []))
                changed = False
                for s in json.loads(row["sources"]):
                    if not any(
                        x.get("doc_id") == s["doc_id"]
                        and x.get("chunk_index") == s["chunk_index"]
                        for x in merged
                    ):
                        merged.append(s)
                        changed = True
                if changed:
                    updates.append(
                        {
                            "name": row["name"],
                            "sources": json.dumps(merged, ensure_ascii=False),
                        }
                    )
            if updates:
                async with self._driver.session() as session:
                    await session.run(
                        f"""
                        UNWIND $updates AS u
                        MATCH (e:{label} {{name: u.name}})
                        SET e.sources = u.sources
                        """,
                        updates=updates,
                    )

    async def upsert_relations_batch(
        self, items: list[tuple[RelationData, EntitySource]]
    ) -> None:
        """批量写入关系：每个 relation_type 一次 UNWIND MERGE + 至多一次 sources 回写。"""
        if not items:
            return
        by_type: dict[str, list[tuple[RelationData, list[dict]]]] = {}
        for relation, sources in _group_relation_items(items):
            by_type.setdefault(relation.relation_type, []).append(
                (relation, sources)
            )

        for relation_type, group in by_type.items():
            rel_label = _quote_cypher_identifier(relation_type, "RELATED_TO")
            rows = [
                {
                    "from_name": r.from_name,
                    "to_name": r.to_name,
                    "description": r.description,
                    "sources": json.dumps(s, ensure_ascii=False),
                }
                for r, s in group
            ]
            async with self._driver.session() as session:
                result = await session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (a {{name: row.from_name}})
                    MATCH (b {{name: row.to_name}})
                    MERGE (a)-[r:{rel_label}]->(b)
                    SET r.description = CASE
                            WHEN size(row.description) > size(coalesce(r.description, ''))
                            THEN row.description
                            ELSE r.description
                        END
                    RETURN row.from_name AS from_name, row.to_name AS to_name,
                           r.sources AS sources
                    """,
                    rows=rows,
                )
                records = await result.data()

            existing_by_pair = {
                (r["from_name"], r["to_name"]): (
                    json.loads(r["sources"]) if r["sources"] else []
                )
                for r in records
            }
            updates = []
            for row in rows:
                merged = list(
                    existing_by_pair.get((row["from_name"], row["to_name"]), [])
                )
                changed = False
                for s in json.loads(row["sources"]):
                    if not any(
                        x.get("doc_id") == s["doc_id"]
                        and x.get("chunk_index") == s["chunk_index"]
                        for x in merged
                    ):
                        merged.append(s)
                        changed = True
                if changed:
                    updates.append(
                        {
                            "from_name": row["from_name"],
                            "to_name": row["to_name"],
                            "sources": json.dumps(merged, ensure_ascii=False),
                        }
                    )
            if updates:
                async with self._driver.session() as session:
                    await session.run(
                        f"""
                        UNWIND $updates AS u
                        MATCH (a {{name: u.from_name}})-[r:{rel_label}]->(b {{name: u.to_name}})
                        SET r.sources = u.sources
                        """,
                        updates=updates,
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
                  AND (NOT neighbor:Document OR neighbor:Document AND coalesce(neighbor.is_current, true) <> false)
                  AND (NOT neighbor:Document OR neighbor.sources IS NULL OR EXISTS {{
                    MATCH (ldn:Document)
                    WHERE coalesce(ldn.is_current, true) <> false
                      AND neighbor.sources CONTAINS ldn.doc_id
                  }})
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
                    (label for label in labels if label != "Document"), "Unknown"
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
                WHERE NOT n:Document
                  AND (n.sources IS NULL OR EXISTS {
                    MATCH (ldn:Document)
                    WHERE coalesce(ldn.is_current, true) <> false
                      AND n.sources CONTAINS ldn.doc_id
                  })
                WITH n
                ORDER BY coalesce(n.entity_type, ''), elementId(n)
                WITH collect(n) AS matches
                UNWIND matches AS matched
                OPTIONAL MATCH (matched)-[r]-(other)
                WITH matches, collect(DISTINCT {
                    type: type(r),
                    direction: CASE
                        WHEN startNode(r) = matched THEN 'OUT'
                        ELSE 'IN'
                    END,
                    other_name: other.name,
                    other_labels: labels(other),
                    description: r.description
                }) AS relations
                WITH head(matches) AS n, relations
                RETURN n, labels(n) AS labels,
                       relations
                """,
                name=name,
            )
            record = await result.single()
            if not record:
                return None

            node = record["n"]
            labels = record["labels"]
            entity_type = next(
                (label for label in labels if label != "Document"), "Unknown"
            )

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
                    (label for label in labels if label not in ("Document",)), "Entity"
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
                        (label for label in labels if label not in ("Document",)), "Entity"
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
                AND EXISTS {
                    MATCH (ld:Document)
                    WHERE coalesce(ld.is_current, true) <> false
                      AND e.sources CONTAINS ld.doc_id
                }
                RETURN e.name AS name, e.description AS description,
                       e.sources AS sources, labels(e) AS labels
                """
            )
            node_records = await node_result.data()
            nodes = []
            for r in node_records:
                labels = r["labels"]
                entity_type = next(
                    (label for label in labels if label != "Document"), "Unknown"
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
                  AND a.sources IS NOT NULL AND b.sources IS NOT NULL
                  AND EXISTS {
                    MATCH (lda:Document)
                    WHERE coalesce(lda.is_current, true) <> false
                      AND a.sources CONTAINS lda.doc_id
                  }
                  AND EXISTS {
                    MATCH (ldb:Document)
                    WHERE coalesce(ldb.is_current, true) <> false
                      AND b.sources CONTAINS ldb.doc_id
                  }
                  AND (r.sources IS NULL OR EXISTS {
                    MATCH (ldr:Document)
                    WHERE coalesce(ldr.is_current, true) <> false
                      AND r.sources CONTAINS ldr.doc_id
                  })
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

    async def find_related_docs_via_entities(
        self, doc_id: str, limit: int = 5
    ) -> list[dict]:
        """查找与本文档共享实体的其他文档（跨文档一致性检查用）。

        sources 是 JSON 字符串，按子串匹配 doc_id（与现有清理逻辑同策略）。
        """
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (e), (d:Document)
                WHERE e.sources IS NOT NULL
                  AND e.sources CONTAINS $doc_id
                  AND d.doc_id <> $doc_id
                  AND d.is_current <> false
                  AND e.sources CONTAINS d.doc_id
                RETURN d.doc_id AS doc_id,
                       d.title AS title,
                       count(DISTINCT e) AS shared_entities
                ORDER BY shared_entities DESC
                LIMIT $limit
                """,
                doc_id=doc_id,
                limit=limit,
            )
            records = await result.data()
            return [
                {
                    "doc_id": r["doc_id"],
                    "title": r["title"],
                    "shared_entities": r["shared_entities"],
                }
                for r in records
            ]
