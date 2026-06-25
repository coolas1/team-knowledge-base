from __future__ import annotations

from dataclasses import dataclass, field

from neo4j import AsyncGraphDatabase

from src.db.config import settings


@dataclass
class EntityData:
    name: str
    entity_type: str
    description: str = ""


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
        """删除 Document 节点及其所有关联的实体和关系。"""
        async with self._driver.session() as session:
            # 先删除 Document 节点及其直接关系
            await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})
                OPTIONAL MATCH (d)-[r]-()
                DELETE r, d
                """,
                doc_id=doc_id,
            )

    # ── 实体 ────────────────────────────────────────────────────

    async def upsert_entity(self, doc_id: str, entity: EntityData) -> None:
        """创建/更新实体节点，并关联到 Document。"""
        async with self._driver.session() as session:
            await session.run(
                f"""
                MERGE (e:{entity.entity_type} {{name: $name}})
                SET e.description = $description
                WITH e
                MATCH (d:Document {{doc_id: $doc_id}})
                MERGE (d)-[:REFERENCES]->(e)
                """,
                name=entity.name,
                description=entity.description,
                doc_id=doc_id,
            )

    # ── 关系 ────────────────────────────────────────────────────

    async def create_relation(self, relation: RelationData) -> None:
        """在两个实体之间创建关系。"""
        async with self._driver.session() as session:
            await session.run(
                f"""
                MATCH (a {{name: $from_name}})
                MATCH (b {{name: $to_name}})
                MERGE (a)-[r:{relation.relation_type}]->(b)
                SET r.description = $description
                """,
                from_name=relation.from_name,
                to_name=relation.to_name,
                description=relation.description,
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
        """获取 Document 关联的所有实体。"""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id})-[:REFERENCES]->(e)
                RETURN e, labels(e) AS labels
                """,
                doc_id=doc_id,
            )
            records = await result.data()
            results = []
            for record in records:
                node = record["e"]
                labels = record["labels"]
                entity_type = labels[0] if labels else "Unknown"
                results.append(
                    GraphQueryResult(
                        name=node.get("name", ""),
                        entity_type=entity_type,
                        properties=dict(node),
                    )
                )
            return results
