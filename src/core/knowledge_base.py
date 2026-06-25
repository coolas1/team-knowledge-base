"""KnowledgeBase 核心业务逻辑层。

被 REST API、MCP Server、Pipeline 共享。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.search import SearchResult, full_search
from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.extractors.registry import ExtractorRegistry
from src.pipeline.pipeline import Pipeline

UPLOAD_DIR = Path("uploads")


class KnowledgeBase:
    """知识库核心业务逻辑。"""

    def __init__(self, neo4j: Neo4jClient) -> None:
        self._neo4j = neo4j
        self._pipeline = Pipeline(neo4j)

    # ── 文件上传 ──────────────────────────────────────────────────

    async def upload_file(
        self,
        session: AsyncSession,
        file_name: str,
        file_content: bytes,
    ) -> dict[str, Any]:
        """上传文件，创建 Document 记录，异步触发 Pipeline。

        Returns:
            {id, title, file_type, status}
        """
        doc_id = uuid.uuid4()
        file_type = ExtractorRegistry.guess_file_type(Path(file_name))

        # 存储原始文件
        doc_dir = UPLOAD_DIR / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        file_path = doc_dir / file_name
        file_path.write_bytes(file_content)

        # 创建 Document 记录
        doc = Document(
            id=doc_id,
            title=file_name,
            file_type=file_type,
            file_path=str(file_path),
            status="pending",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        # 异步触发 Pipeline（不阻塞请求）
        import asyncio
        asyncio.create_task(
            self._pipeline.process_file(doc_id, file_path, file_name, file_type)
        )

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": file_type,
            "status": doc.status,
        }

    # ── 内容编辑 ──────────────────────────────────────────────────

    async def edit_content(
        self,
        session: AsyncSession,
        doc_id: uuid.UUID,
        new_content: str,
    ) -> dict[str, Any]:
        """在线编辑 md 内容，触发 re-index。

        Returns:
            {id, title, status}
        """
        doc = await session.get(Document, doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        # 更新 raw_text
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(raw_text=new_content, status="pending")
        )
        await session.commit()

        # 异步 re-index
        import asyncio
        asyncio.create_task(
            self._pipeline.reindex_document(doc_id, new_content)
        )

        return {"id": str(doc.id), "title": doc.title, "status": "pending"}

    # ── 文件查询 ──────────────────────────────────────────────────

    async def get_document(
        self, session: AsyncSession, doc_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """获取文件详情（含 chunks 摘要）。"""
        doc = await session.get(Document, doc_id)
        if not doc:
            return None

        # 查询 chunks 数量
        count_stmt = (
            select(func.count(Chunk.id)).where(Chunk.doc_id == doc_id)
        )
        count_result = await session.execute(count_stmt)
        chunk_count = count_result.scalar() or 0

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": doc.file_type,
            "raw_text": doc.raw_text,
            "overview": doc.overview,
            "file_path": doc.file_path,
            "content_hash": doc.content_hash,
            "status": doc.status,
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
    ) -> dict[str, Any]:
        """文件列表（分页，可按 type/status 筛选）。"""
        stmt = select(Document).order_by(Document.created_at.desc())

        if file_type:
            stmt = stmt.where(Document.file_type == file_type)
        if status:
            stmt = stmt.where(Document.status == status)

        # 总数
        count_stmt = select(func.count(Document.id))
        if file_type:
            count_stmt = count_stmt.where(Document.file_type == file_type)
        if status:
            count_stmt = count_stmt.where(Document.status == status)
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
                    "overview": d.overview[:200] if d.overview else "",
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                }
                for d in docs
            ],
        }

    # ── 删除 ─────────────────────────────────────────────────────

    async def delete_document(
        self, session: AsyncSession, doc_id: uuid.UUID
    ) -> bool:
        """删除文件（级联删 chunks + Neo4j + 本地文件）。"""
        doc = await session.get(Document, doc_id)
        if not doc:
            return False

        # 删除本地文件
        if doc.file_path:
            doc_dir = Path(doc.file_path).parent
            if doc_dir.exists():
                shutil.rmtree(doc_dir)

        # 删除 Postgres（cascade 会删 chunks）
        await session.delete(doc)
        await session.commit()

        # 删除 Neo4j
        await self._neo4j.delete_document_graph(str(doc_id))

        return True

    # ── 搜索 ─────────────────────────────────────────────────────

    async def search(
        self, session: AsyncSession, query: str
    ) -> SearchResult:
        """三层漏斗语义检索。"""
        return await full_search(session, self._neo4j, query)

    # ── 图谱 ─────────────────────────────────────────────────────

    async def get_entity(self, name: str) -> dict[str, Any] | None:
        """查询实体详情 + 关联关系。"""
        result = await self._neo4j.get_entity_details(name)
        if not result:
            return None
        return {
            "name": result.name,
            "type": result.entity_type,
            "properties": result.properties,
            "relations": result.relations,
        }

    async def get_neighbors(
        self, name: str, hops: int = 2
    ) -> list[dict[str, Any]]:
        """获取实体 N 跳邻居。"""
        results = await self._neo4j.query_neighbors(name, hops)
        return [
            {
                "name": r.name,
                "type": r.entity_type,
                "properties": r.properties,
            }
            for r in results
        ]
