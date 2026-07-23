"""KnowledgeBase 核心业务逻辑层。

被 REST API、MCP Server、Pipeline 共享。
"""

from __future__ import annotations

import shutil
import uuid
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.search import SearchResult, full_search
from src.core.operations import OperationManager
from src.core.projector import Neo4jProjector
from src.db.models import Chunk, Document, OutboxEvent, Team
from src.db.neo4j_client import Neo4jClient
from src.pipeline.extractors.registry import ExtractorRegistry
from src.pipeline.pipeline import Pipeline

UPLOAD_DIR = Path("uploads")


class KnowledgeBase:
    """知识库核心业务逻辑。"""

    def __init__(self, neo4j: Neo4jClient) -> None:
        self._neo4j = neo4j
        self._pipeline = Pipeline(neo4j)
        self.operations = OperationManager(self._pipeline)
        self.projector = Neo4jProjector(neo4j)

    async def start(self) -> None:
        await self.operations.start()
        await self.projector.start()

    async def stop(self) -> None:
        await self.projector.stop()
        await self.operations.stop()

    @staticmethod
    async def _ensure_team(session: AsyncSession, team_id: str) -> None:
        if not await session.get(Team, team_id):
            session.add(Team(id=team_id, name=team_id))
            await session.flush()

    # ── 文件上传 ──────────────────────────────────────────────────

    async def upload_file(
        self,
        session: AsyncSession,
        file_name: str,
        file_content: bytes,
        team_id: str = "default",
        tags: list[str] | None = None,
        scope: str = "team",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """上传文件，创建 Document 记录，异步触发 Pipeline。

        Returns:
            {id, title, file_type, status}
        """
        if scope not in {"team", "public"}:
            raise ValueError("scope 仅支持 team/public")
        await self._ensure_team(session, team_id)
        content_hash = hashlib.sha256(file_content).hexdigest()
        hash_payload = {
            "file_name": file_name,
            "content_hash": content_hash,
            "tags": sorted(set(tags or [])),
            "scope": scope,
        }
        existing = await self.operations.find_idempotent(
            session,
            team_id=team_id,
            idempotency_key=idempotency_key,
            hash_payload=hash_payload,
        )
        if existing:
            return {
                "id": str(existing.document_id),
                "operation_id": str(existing.id),
                "status": existing.status,
                "idempotent_replay": True,
            }

        doc_id = uuid.uuid4()
        file_type = ExtractorRegistry.guess_file_type(Path(file_name))

        # 存储原始文件
        doc_dir = UPLOAD_DIR / team_id / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        file_path = doc_dir / file_name
        file_path.write_bytes(file_content)

        # 创建 Document 记录
        doc = Document(
            id=doc_id,
            team_id=team_id,
            title=file_name,
            file_type=file_type,
            file_path=str(file_path),
            status="pending",
            graph_status="pending",
            tags=sorted(set(tags or [])),
            scope=scope,
        )
        session.add(doc)
        operation = await self.operations.enqueue(
            session,
            team_id=team_id,
            operation_type="index_document",
            document_id=doc_id,
            idempotency_key=idempotency_key,
            hash_payload=hash_payload,
            payload={
                "doc_id": str(doc_id),
                "file_path": str(file_path),
                "title": file_name,
                "file_type": file_type,
            },
        )
        await session.commit()
        await session.refresh(doc)

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": file_type,
            "status": doc.status,
            "operation_id": str(operation.id),
            "team_id": team_id,
            "tags": doc.tags,
            "scope": doc.scope,
        }

    # ── 内容编辑 ──────────────────────────────────────────────────

    async def edit_content(
        self,
        session: AsyncSession,
        doc_id: uuid.UUID,
        new_content: str,
        team_id: str = "default",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """在线编辑 md 内容，触发 re-index。

        Returns:
            {id, title, status}
        """
        doc = await session.scalar(
            select(Document).where(Document.id == doc_id, Document.team_id == team_id)
        )
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        # 更新 raw_text
        await session.execute(
            update(Document)
            .where(Document.id == doc_id, Document.team_id == team_id)
            .values(raw_text=new_content, status="pending")
        )
        hash_payload = {
            "doc_id": str(doc_id),
            "content_hash": hashlib.sha256(new_content.encode()).hexdigest(),
            "document_version": (doc.version or 1) + 1,
        }
        operation = await self.operations.enqueue(
            session,
            team_id=team_id,
            operation_type="reindex_document",
            document_id=doc_id,
            idempotency_key=idempotency_key,
            hash_payload=hash_payload,
            payload={"doc_id": str(doc_id), "content": new_content},
        )
        await session.commit()

        return {
            "id": str(doc.id),
            "title": doc.title,
            "status": "pending",
            "operation_id": str(operation.id),
        }

    # ── 文件查询 ──────────────────────────────────────────────────

    async def get_document(
        self, session: AsyncSession, doc_id: uuid.UUID, team_id: str = "default"
    ) -> dict[str, Any] | None:
        """获取文件详情（含 chunks 摘要）。"""
        doc = await session.scalar(select(Document).where(
            Document.id == doc_id,
            or_(Document.team_id == team_id, Document.scope == "public"),
        ))
        if not doc:
            return None

        # 查询 chunks 数量
        count_stmt = (
            select(func.count(Chunk.id)).where(
                Chunk.doc_id == doc_id, Chunk.team_id == doc.team_id
            )
        )
        count_result = await session.execute(count_stmt)
        chunk_count = count_result.scalar() or 0

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": doc.file_type,
            "raw_text": doc.raw_text,
            "overview": doc.overview,
            "file_path": doc.file_path if doc.team_id == team_id else None,
            "content_hash": doc.content_hash,
            "status": doc.status,
            "graph_status": doc.graph_status,
            "team_id": doc.team_id,
            "read_only": doc.team_id != team_id,
            "scope": doc.scope,
            "tags": doc.tags,
            "version": doc.version,
            "error_msg": doc.error_msg,
            "chunk_count": chunk_count,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }

    async def list_documents(
        self,
        session: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
        team_id: str = "default",
        tags: list[str] | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """文件列表（分页，可按 type/status 筛选）。"""
        visibility = or_(Document.team_id == team_id, Document.scope == "public")
        stmt = select(Document).where(visibility).order_by(Document.created_at.desc())

        if file_type:
            stmt = stmt.where(Document.file_type == file_type)
        if status:
            stmt = stmt.where(Document.status == status)
        if scope:
            stmt = stmt.where(Document.scope == scope)
        if tags:
            stmt = stmt.where(or_(*(Document.tags.contains([tag]) for tag in tags)))

        # 总数
        count_stmt = select(func.count(Document.id)).where(visibility)
        if file_type:
            count_stmt = count_stmt.where(Document.file_type == file_type)
        if status:
            count_stmt = count_stmt.where(Document.status == status)
        if scope:
            count_stmt = count_stmt.where(Document.scope == scope)
        if tags:
            count_stmt = count_stmt.where(or_(*(Document.tags.contains([tag]) for tag in tags)))
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        # 分页
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await session.execute(stmt)
        docs = result.scalars().all()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": str(d.id),
                    "title": d.title,
                    "file_type": d.file_type,
                    "status": d.status,
                    "graph_status": d.graph_status,
                    "team_id": d.team_id,
                    "scope": d.scope,
                    "tags": d.tags,
                    "overview": d.overview[:200] if d.overview else "",
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                }
                for d in docs
            ],
        }

    # ── 删除 ─────────────────────────────────────────────────────

    async def delete_document(
        self, session: AsyncSession, doc_id: uuid.UUID, team_id: str = "default"
    ) -> bool:
        """删除文件（级联删 chunks + Neo4j + 本地文件）。"""
        doc = await session.scalar(
            select(Document).where(Document.id == doc_id, Document.team_id == team_id)
        )
        if not doc:
            return False

        # 删除本地文件
        if doc.file_path:
            doc_dir = Path(doc.file_path).parent
            if doc_dir.exists():
                shutil.rmtree(doc_dir)

        # 删除 Postgres（cascade 会删 chunks）
        session.add(
            OutboxEvent(
                team_id=team_id,
                aggregate_type="document",
                aggregate_id=str(doc_id),
                aggregate_version=(doc.version or 1) + 1,
                event_type="document_graph_delete_requested",
                payload={
                    "document_id": str(doc_id),
                    "projection_team_id": "public" if doc.scope == "public" else team_id,
                },
            )
        )
        await session.delete(doc)
        await session.commit()

        return True

    # ── 搜索 ─────────────────────────────────────────────────────

    async def search(
        self,
        session: AsyncSession,
        query: str,
        team_id: str = "default",
        team_ids: list[str] | tuple[str, ...] | None = None,
        tags: list[str] | None = None,
        scopes: list[str] | None = None,
        include_graph: bool = True,
    ) -> SearchResult:
        """三层漏斗语义检索。"""
        return await full_search(
            session,
            self._neo4j,
            query,
            team_id=team_id,
            team_ids=team_ids,
            tags=tags,
            scopes=scopes,
            include_graph=include_graph,
        )

    # ── 图谱 ─────────────────────────────────────────────────────

    async def get_entity(self, name: str, team_id: str = "default") -> dict[str, Any] | None:
        """查询实体详情 + 关联关系。"""
        result = await self._neo4j.get_entity_details(name, team_id)
        graph_namespace = team_id
        if not result:
            result = await self._neo4j.get_entity_details(name, "public")
            graph_namespace = "public"
        if not result:
            return None
        return {
            "name": result.name,
            "type": result.entity_type,
            "properties": result.properties,
            "relations": result.relations,
            "graph_namespace": graph_namespace,
        }

    async def get_neighbors(
        self, name: str, hops: int = 2, team_id: str = "default"
    ) -> list[dict[str, Any]]:
        """获取实体 N 跳邻居。"""
        results = await self._neo4j.query_neighbors(name, hops, team_id)
        if not results:
            results = await self._neo4j.query_neighbors(name, hops, "public")
        return [
            {
                "name": r.name,
                "type": r.entity_type,
                "properties": r.properties,
            }
            for r in results
        ]

    async def get_full_graph(self, team_id: str = "default") -> dict[str, Any]:
        """返回当前团队私有图谱与独立公共投影的合并视图。"""
        team_graph = await self._neo4j.get_full_graph(team_id)
        public_graph = await self._neo4j.get_full_graph("public")

        def namespaced(graph: dict[str, Any], namespace: str) -> dict[str, Any]:
            nodes = [
                {
                    **node,
                    "id": f"{namespace}:{node['name']}",
                    "namespace": namespace,
                }
                for node in graph["nodes"]
            ]
            links = [
                {
                    **link,
                    "source": f"{namespace}:{link['source']}",
                    "target": f"{namespace}:{link['target']}",
                    "namespace": namespace,
                }
                for link in graph["links"]
            ]
            return {"nodes": nodes, "links": links}

        private_view = namespaced(team_graph, team_id)
        public_view = namespaced(public_graph, "public")
        return {
            "team_id": team_id,
            "includes_public": True,
            "nodes": private_view["nodes"] + public_view["nodes"],
            "links": private_view["links"] + public_view["links"],
            "counts": {
                "team_nodes": len(private_view["nodes"]),
                "public_nodes": len(public_view["nodes"]),
            },
        }
