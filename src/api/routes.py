"""REST API 路由。"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.knowledge_base import KnowledgeBase
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import get_session

router = APIRouter()

# ── 依赖注入 ────────────────────────────────────────────────────

_kb_instance: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb_instance
    _kb_instance = kb


def get_kb() -> KnowledgeBase:
    if _kb_instance is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb_instance


# ── Schemas ─────────────────────────────────────────────────────


class EditContentRequest(BaseModel):
    content: str


class SearchRequest(BaseModel):
    query: str


# ── 文件端点 ────────────────────────────────────────────────────


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """上传文件 → 返回 doc_id + status。"""
    content = await file.read()
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    return await kb.upload_file(session, file.filename, content)


@router.put("/documents/{doc_id}/content")
async def edit_content(
    doc_id: uuid.UUID,
    body: EditContentRequest,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """在线编辑 md → 触发 re-index。"""
    try:
        return await kb.edit_content(session, doc_id, body.content)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """文件详情（含 status、overview、chunk 数量）。"""
    result = await kb.get_document(session, doc_id)
    if not result:
        raise HTTPException(404, "文档不存在")
    return result


@router.get("/documents")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """文件列表（分页，按 type/status 筛选）。"""
    return await kb.list_documents(session, page, page_size, file_type, status)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j）。"""
    deleted = await kb.delete_document(session, doc_id)
    if not deleted:
        raise HTTPException(404, "文档不存在")
    return {"deleted": True, "id": str(doc_id)}


# ── 搜索端点 ────────────────────────────────────────────────────


@router.post("/search")
async def search(
    body: SearchRequest,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """语义检索（三层漏斗）。"""
    result = await kb.search(session, body.query)
    return {
        "answer": result.answer,
        "sources": [
            {
                "doc_id": s.doc_id,
                "title": s.title,
                "chunk_text": s.chunk_text,
                "score": s.score,
            }
            for s in result.sources
        ],
        "related_entities": result.related_entities,
    }


# ── 图谱端点 ────────────────────────────────────────────────────


@router.get("/graph/entity/{name}")
async def get_entity(
    name: str,
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """查询实体详情 + 关联关系。"""
    result = await kb.get_entity(name)
    if not result:
        raise HTTPException(404, f"实体不存在: {name}")
    return result


@router.get("/graph/neighbors/{name}")
async def get_neighbors(
    name: str,
    hops: int = Query(2, ge=1, le=3),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """获取实体 N 跳邻居。"""
    results = await kb.get_neighbors(name, hops)
    return {"name": name, "hops": hops, "neighbors": results}
