"""REST API 路由。"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.knowledge_base import KnowledgeBase
from src.core.log_manager import log_manager
from src.core.version_manager import VersionManager
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import get_session

router = APIRouter()

# ── 依赖注入 ────────────────────────────────────────────────────

_kb_instance: KnowledgeBase | None = None
_version_manager: VersionManager | None = None
_scheduler_instance = None  # PipelineScheduler


def set_kb(kb: KnowledgeBase) -> None:
    global _kb_instance
    _kb_instance = kb


def set_version_manager(vm: VersionManager) -> None:
    global _version_manager
    _version_manager = vm


def set_scheduler(scheduler) -> None:
    global _scheduler_instance
    _scheduler_instance = scheduler


def get_kb() -> KnowledgeBase:
    if _kb_instance is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb_instance


def get_version_manager() -> VersionManager:
    if _version_manager is None:
        raise RuntimeError("VersionManager 未初始化")
    return _version_manager


def get_scheduler():
    if _scheduler_instance is None:
        raise RuntimeError("PipelineScheduler 未初始化")
    return _scheduler_instance


# ── Schemas ─────────────────────────────────────────────────────


class EditContentRequest(BaseModel):
    content: str


class SearchRequest(BaseModel):
    query: str


class DiagnoseRequest(BaseModel):
    query: str
    gold_filenames: list[str] = []


# ── 文件端点 ────────────────────────────────────────────────────


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """上传文件 → 返回 doc_id + index_status。"""
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
    """文件详情（含 index_status、overview、chunk 数量）。"""
    result = await kb.get_document(session, doc_id)
    if not result:
        raise HTTPException(404, "文档不存在")
    return result


@router.get("/documents")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None,
    index_status: str | None = None,
    file_status: str | None = None,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """文件列表（分页，按 type/index_status/file_status 筛选）。"""
    return await kb.list_documents(session, page, page_size, file_type, index_status, file_status)


@router.get("/documents/{doc_id}/file")
async def download_document_file(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> FileResponse:
    """下载/查看原始文件。"""
    result = await kb.get_document(session, doc_id)
    if not result:
        raise HTTPException(404, "文档不存在")
    file_path = result.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(404, "原始文件不存在")

    # 根据扩展名推断 media_type
    ext = Path(file_path).suffix.lower()
    media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    # 图片和 PDF 在浏览器中内联展示，其他类型触发下载
    inline_types = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".pdf"}
    disposition = "inline" if ext in inline_types else "attachment"

    # 处理非 ASCII 文件名（RFC 5987 编码）
    filename = result.get("title", "download")
    encoded_filename = quote(filename)

    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_filename}"},
    )


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """删除文件（软删除，标记 disappeared）。"""
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
    """语义检索（向量粗筛 → Reranker 守门 → 图谱增强）。"""
    result = await kb.search(session, body.query)
    return {
        "chunks": [
            {
                "doc_id": c.doc_id,
                "title": c.title,
                "chunk_text": c.chunk_text,
                "reranker_score": c.reranker_score,
                "vector_score": c.vector_score,
                "index_status": c.index_status,
            }
            for c in result.chunks
        ],
        "related_entities": result.related_entities,
        "related_docs": result.related_docs,
        "debug": result.debug,
    }


@router.post("/search/diagnose")
async def search_diagnose(
    body: DiagnoseRequest,
) -> dict[str, Any]:
    """检索诊断：逐段追踪目标文档 + 耗时分布 + 内容质量。"""
    from diag_pipeline import run_diagnosis, DiagReport

    report: DiagReport = await run_diagnosis(body.query, body.gold_filenames)

    # 序列化为 JSON 友好的结构
    stages = []
    for s in report.stages:
        stages.append({
            "name": s.name,
            "elapsed_ms": round(s.elapsed_ms, 1),
            "target_hit": s.target_hit,
            "target_rank": s.target_rank,
            "target_score": round(s.target_score, 4),
            "total_candidates": s.total_candidates,
            "path_hits": {k: {"rank": v[0], "score": round(v[1], 4)} for k, v in s.path_hits.items()},
            "extra": s.extra,
        })

    return {
        "query": report.query,
        "gold_filenames": report.gold_filenames,
        "verdict": report.verdict,
        "final_rank": report.final_rank,
        "total_ms": round(report.total_ms, 1),
        "stages": stages,
        "content_quality": report.content_quality,
    }


# ── 图谱端点 ────────────────────────────────────────────────────


@router.get("/graph/full")
async def get_full_graph(
    kb: KnowledgeBase = Depends(get_kb),
) -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系），供前端力导向图渲染。"""
    return await kb.get_full_graph()


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


# ── 日志端点 ──────────────────────────────────────────────────────


@router.get("/logs")
async def list_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    level: str | None = None,
    doc_id: str | None = None,
    trace_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict[str, Any]:
    """分页查询日志（支持按级别、时间范围、文档ID、trace_id过滤）。"""
    st = datetime.fromisoformat(start_time) if start_time else None
    et = datetime.fromisoformat(end_time) if end_time else None
    return await log_manager.query_logs(
        page=page,
        page_size=page_size,
        level=level,
        doc_id=doc_id,
        trace_id=trace_id,
        start_time=st,
        end_time=et,
    )


@router.get("/logs/stream")
async def stream_logs() -> StreamingResponse:
    """通过 SSE 实时推送日志。"""
    return StreamingResponse(
        log_manager.stream_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


@router.delete("/logs")
async def clear_logs(keep_days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    """清理旧日志，仅保留最近 keep_days 天。"""
    deleted = await log_manager.cleanup(keep_days=keep_days)
    return {"deleted_count": deleted, "keep_days": keep_days}


# ── 版本管理端点 ────────────────────────────────────────────────


@router.get("/documents/{doc_id}/versions")
async def list_versions(
    doc_id: uuid.UUID,
    vm: VersionManager = Depends(get_version_manager),
) -> dict[str, Any]:
    """获取文档的版本列表。"""
    return await vm.list_versions(doc_id)


@router.get("/documents/{doc_id}/versions/{version}")
async def get_version(
    doc_id: uuid.UUID,
    version: int,
    vm: VersionManager = Depends(get_version_manager),
) -> dict[str, Any]:
    """获取指定版本的详细内容。"""
    result = await vm.get_version(doc_id, version)
    if not result:
        raise HTTPException(404, f"版本 v{version} 不存在")
    return result


@router.get("/documents/{doc_id}/versions/{version}/file")
async def get_version_file(
    doc_id: uuid.UUID,
    version: int,
    vm: VersionManager = Depends(get_version_manager),
) -> FileResponse:
    """下载指定版本的原始文件快照。"""
    file_path = await vm.get_version_file(doc_id, version)
    if not file_path:
        raise HTTPException(404, f"版本 v{version} 的快照文件不存在")
    return FileResponse(path=file_path)


@router.get("/documents/{doc_id}/versions/diff")
async def get_version_diff(
    doc_id: uuid.UUID,
    from_version: int = Query(..., alias="from"),
    to_version: int = Query(..., alias="to"),
    vm: VersionManager = Depends(get_version_manager),
) -> dict[str, Any]:
    """计算两个版本之间的 unified diff。"""
    result = await vm.get_diff(doc_id, from_version, to_version)
    if not result:
        raise HTTPException(404, "指定版本不存在")
    return result


@router.post("/documents/{doc_id}/versions/{version}/rollback")
async def rollback_version(
    doc_id: uuid.UUID,
    version: int,
    vm: VersionManager = Depends(get_version_manager),
) -> dict[str, Any]:
    """回滚到指定版本。"""
    result = await vm.rollback(doc_id, version)
    if not result:
        raise HTTPException(404, f"版本 v{version} 不存在")
    return result


# ── 同步控制端点 ────────────────────────────────────────────────


@router.post("/sync")
async def trigger_sync(
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    """手动触发 Pipeline 同步。"""
    return await scheduler.trigger_manual()


@router.get("/sync/status")
async def sync_status(
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    """获取同步状态。"""
    return await scheduler.get_status()
