"""KnowledgeBase 核心业务逻辑层。

被 REST API、MCP Server、Pipeline 共享。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.search import SearchResult, full_search
from src.db.models import Chunk, Document, DocumentVersion
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.extractors.registry import ExtractorRegistry
from src.pipeline.pipeline import Pipeline

UPLOAD_DIR = Path("uploads")
logger = logging.getLogger(__name__)

# 保持后台任务引用，防止被 GC 回收
_background_tasks: set[asyncio.Task] = set()


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
            {id, title, file_type, index_status}
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
            source_type="manual",
            index_status="pending",
            file_status="active",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)

        logger.info(
            f"文档上传: {doc_id} | 文件名={file_name} | 类型={file_type} | "
            f"大小={len(file_content)}字节 | 状态=pending"
        )

        # 异步触发 Pipeline（不阻塞请求）
        task = asyncio.create_task(
            self._pipeline.process_file(doc_id, file_path, file_name, file_type)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": file_type,
            "index_status": doc.index_status,
            "file_status": doc.file_status,
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
            {id, title, index_status}
        """
        doc = await session.get(Document, doc_id)
        if not doc:
            raise ValueError(f"文档不存在: {doc_id}")

        # 更新 raw_text
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(raw_text=new_content, index_status="stale")
        )
        await session.commit()

        # 异步 re-index
        task = asyncio.create_task(
            self._pipeline.reindex_document(doc_id, new_content)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info(f"文档编辑触发 re-index: {doc_id} | 内容长度={len(new_content)}字符")

        return {"id": str(doc.id), "title": doc.title, "index_status": "stale"}

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

        # 查询版本数量
        version_count_stmt = (
            select(func.count(DocumentVersion.id)).where(DocumentVersion.doc_id == doc_id)
        )
        version_count_result = await session.execute(version_count_stmt)
        version_count = version_count_result.scalar() or 0

        return {
            "id": str(doc.id),
            "title": doc.title,
            "file_type": doc.file_type,
            "raw_text": doc.raw_text,
            "overview": doc.overview,
            "file_path": doc.file_path,
            "content_hash": doc.content_hash,
            "source_type": doc.source_type,
            "source_path": doc.source_path,
            "index_status": doc.index_status,
            "file_status": doc.file_status,
            "error_msg": doc.error_msg,
            "chunk_count": chunk_count,
            "version_count": version_count,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }

    async def list_documents(
        self,
        session: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        index_status: str | None = None,
        file_status: str | None = None,
    ) -> dict[str, Any]:
        """文件列表（分页，可按 type/index_status/file_status 筛选）。"""
        stmt = select(Document).order_by(Document.created_at.desc())

        if file_type:
            stmt = stmt.where(Document.file_type == file_type)
        if index_status:
            stmt = stmt.where(Document.index_status == index_status)
        if file_status:
            stmt = stmt.where(Document.file_status == file_status)

        # 总数
        count_stmt = select(func.count(Document.id))
        if file_type:
            count_stmt = count_stmt.where(Document.file_type == file_type)
        if index_status:
            count_stmt = count_stmt.where(Document.index_status == index_status)
        if file_status:
            count_stmt = count_stmt.where(Document.file_status == file_status)
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
                    "index_status": d.index_status,
                    "file_status": d.file_status,
                    "source_type": d.source_type,
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
        """软删除文件（标记 file_status=disappeared，保留版本历史和索引）。"""
        doc = await session.get(Document, doc_id)
        if not doc:
            return False

        # 软删除：标记 file_status=disappeared
        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(file_status="disappeared")
        )
        await session.commit()

        logger.info(f"文档软删除: {doc_id} | 标题={doc.title} | 类型={doc.file_type}")

        return True

    async def hard_delete_document(
        self, session: AsyncSession, doc_id: uuid.UUID
    ) -> bool:
        """硬删除文件（级联删 chunks + Neo4j + 本地文件 + 版本历史）。"""
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

        logger.info(f"文档删除: {doc_id} | 标题={doc.title} | 类型={doc.file_type}")

        return True

    # ── 搜索 ─────────────────────────────────────────────────────

    async def search(
        self, session: AsyncSession, query: str
    ) -> SearchResult:
        """三层漏斗语义检索。"""
        logger.info(f"搜索请求: query='{query[:80]}' | 长度={len(query)}")
        result = await full_search(session, self._neo4j, query)
        logger.info(
            f"搜索完成: query='{query[:80]}' | "
            f"返回 {len(result.chunks)} chunks, "
            f"{len(result.related_entities)} 实体, "
            f"{len(result.related_docs)} 关联文档"
        )
        return result

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

    async def get_full_graph(self) -> dict[str, Any]:
        """返回全图数据（nodes + links）。"""
        return await self._neo4j.get_full_graph()
