"""Webapp host document routes: call the in-process KnowledgeBase."""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.engine.interface import IngestSource, KnowledgeBase
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

SUPPORTED_UPLOAD_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".pdf",
    ".docx",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".tiff",
    ".bmp",
    ".webp",
}


def _upload_error(
    status_code: int,
    code: str,
    message: str,
    suggestion: str,
    *,
    retryable: bool,
) -> HTTPException:
    return HTTPException(
        status_code,
        detail={
            "code": code,
            "message": message,
            "suggestion": suggestion,
            "retryable": retryable,
        },
    )


class EditContentRequest(BaseModel):
    content: str


@router.get("")
async def list_documents(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None, status: str | None = None,
    kb: KnowledgeBase = Depends(deps.get_kb),
):
    return await kb.list_documents(page, page_size, file_type, status)


@router.get("/{doc_id}")
async def get_document(doc_id: str, kb: KnowledgeBase = Depends(deps.get_kb)):
    out = await kb.get_document(doc_id)
    if out is None:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    # Pipeline progress: in-memory (engine runs in-process).
    from src.engine.graphrag.progress import get_progress

    p = get_progress(doc_id)
    if p is not None:
        out["pipeline"] = p
    return out


def _upload_file_error(filename: str | None, data: bytes) -> tuple[int, dict] | None:
    """上传文件的通用校验：返回 (status, detail) 或 None（通过）。"""
    if not filename:
        return (
            400,
            {
                "code": "missing_filename",
                "message": "未读取到文件名",
                "suggestion": "请重新选择本地文件后再试。",
                "retryable": False,
            },
        )
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_UPLOAD_EXTENSIONS:
        formats = "、".join(sorted(SUPPORTED_UPLOAD_EXTENSIONS))
        return (
            400,
            {
                "code": "unsupported_file_type",
                "message": f"不支持 {extension or '无扩展名'} 文件",
                "suggestion": f"请选择以下格式：{formats}。",
                "retryable": False,
            },
        )
    if not data:
        return (
            400,
            {
                "code": "empty_file",
                "message": "文件内容为空",
                "suggestion": "请确认文件包含内容，保存后重新选择该文件。",
                "retryable": False,
            },
        )
    return None


async def _ingest_uploaded(kb: KnowledgeBase, filename: str, data: bytes):
    """调用入库并归一错误为 (status, detail)；成功返回 DocumentRef。"""
    try:
        return await kb.ingest(IngestSource(name=filename, data=data))
    except ValueError as exc:
        raise _upload_error(
            400,
            "invalid_file",
            str(exc),
            "请确认文件未损坏且可正常打开，然后重新选择文件。",
            retryable=False,
        ) from exc
    except Exception as exc:
        logger.exception("上传文件 %s 失败", filename)
        raise _upload_error(
            503,
            "upload_service_unavailable",
            "上传服务暂时不可用",
            "请稍后直接重试；如果持续失败，请检查数据库和存储服务状态。",
            retryable=True,
        ) from exc


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), kb: KnowledgeBase = Depends(deps.get_kb)):
    data = await file.read()
    error = _upload_file_error(file.filename, data)
    if error is not None:
        status_code, detail = error
        raise HTTPException(status_code, detail=detail)
    ref = await _ingest_uploaded(kb, file.filename, data)
    return asdict(ref)


@router.post("/upload/batch")
async def upload_documents_batch(
    files: list[UploadFile] = File(...), kb: KnowledgeBase = Depends(deps.get_kb)
):
    """批量上传：逐文件校验并隔离失败，一个坏文件不影响其余文件。

    空请求（0 个文件）由 FastAPI 在参数校验层以 422 拒绝。
    """
    items: list[dict] = []
    for file in files:
        data = await file.read()
        error = _upload_file_error(file.filename, data)
        if error is not None:
            _status, detail = error
            items.append({"ok": False, "error": detail})
            continue
        try:
            ref = await _ingest_uploaded(kb, file.filename, data)
        except HTTPException as exc:
            items.append({"ok": False, "error": exc.detail})
            continue
        items.append({"ok": True, "document": asdict(ref)})
    return {"items": items}


@router.put("/{doc_id}/content")
async def edit_document_content(
    doc_id: str,
    body: EditContentRequest,
    kb: KnowledgeBase = Depends(deps.get_kb),
):
    try:
        return asdict(await kb.edit_content(doc_id, body.content))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{doc_id}/retry")
async def retry_document(doc_id: str, kb: KnowledgeBase = Depends(deps.get_kb)):
    try:
        return asdict(await kb.reingest(doc_id))
    except ValueError as exc:
        raise _upload_error(
            400,
            "document_not_retryable",
            str(exc),
            "请重新选择原文件上传。",
            retryable=False,
        ) from exc
    except Exception as exc:
        logger.exception("重新处理文档 %s 失败", doc_id)
        raise _upload_error(
            503,
            "retry_service_unavailable",
            "暂时无法重新处理文件",
            "请稍后重试；如果持续失败，请检查数据库、模型和存储服务状态。",
            retryable=True,
        ) from exc


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, kb: KnowledgeBase = Depends(deps.get_kb)):
    await kb.remove(doc_id)
    return {"removed": doc_id}
