"""归档 API：待审/留置/历史/撤销/模式/目录树 + inbox 上传入口。

所有变更端点走与流水线相同的校验、执行、journal 代码
（spec archiving-review: API and pipeline share execution semantics）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.frontend.webapp.server import deps

router = APIRouter(prefix="/archive", tags=["archive"])
logger = logging.getLogger(__name__)

# 与文档上传一致的格式白名单（routes_documents.SUPPORTED_UPLOAD_EXTENSIONS）。
SUPPORTED_INBOX_EXTENSIONS = {
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


def _require_archive(archive=Depends(deps.get_archive)):
    return archive


def _bad_request(message: str, code: str = "invalid_request") -> HTTPException:
    return HTTPException(400, {"code": code, "message": message})


class ReassignRequest(BaseModel):
    directory: str


class ModeRequest(BaseModel):
    review_all: bool


class PolicyRequest(BaseModel):
    enabled: bool = True
    rules: dict


class LegacyPlanRequest(BaseModel):
    document_ids: list[str] | None = None


class LegacyExecuteRequest(BaseModel):
    batch_id: str
    document_ids: list[str] | None = None
    overrides: dict[str, dict] | None = None


@router.get("/reviews")
async def list_reviews(archive=Depends(_require_archive)):
    return {"items": await archive.list_reviews()}


@router.post("/reviews/{job_id}/approve")
async def approve_review(job_id: str, archive=Depends(_require_archive)):
    try:
        return await archive.approve(job_id)
    except ValueError as exc:
        raise _bad_request(str(exc), "job_not_reviewable") from exc
    except Exception as exc:
        logger.exception("批准归档 %s 失败", job_id)
        raise HTTPException(
            503,
            {
                "code": "archive_execute_unavailable",
                "message": "执行归档操作失败",
                "retryable": True,
            },
        ) from exc


@router.post("/reviews/{job_id}/reject")
async def reject_review(job_id: str, archive=Depends(_require_archive)):
    """旧客户端兼容别名；V2 中 reject 的实际语义为 defer。"""
    try:
        return await archive.reject(job_id)
    except ValueError as exc:
        raise _bad_request(str(exc), "job_not_reviewable") from exc


@router.post("/reviews/{job_id}/defer")
async def defer_review(job_id: str, archive=Depends(_require_archive)):
    try:
        return await archive.defer(job_id)
    except ValueError as exc:
        raise _bad_request(str(exc), "job_not_reviewable") from exc


@router.post("/reviews/{job_id}/reassign")
async def reassign_review(
    job_id: str, body: ReassignRequest, archive=Depends(_require_archive)
):
    try:
        return await archive.reassign(job_id, body.directory)
    except ValueError as exc:
        raise _bad_request(str(exc), "reassign_invalid") from exc
    except Exception as exc:
        logger.exception("改分类 %s 失败", job_id)
        raise HTTPException(
            503,
            {
                "code": "archive_execute_unavailable",
                "message": "执行归档操作失败",
                "retryable": True,
            },
        ) from exc


@router.get("/skipped")
async def list_skipped(archive=Depends(_require_archive)):
    """未归档 + 失败/超限：留在 inbox 需要人处理的文件。"""
    return {"items": await archive.list_attention()}


@router.get("/unarchived")
async def list_unarchived(archive=Depends(_require_archive)):
    return {"items": await archive.list_unarchived()}


@router.post("/unarchived/{job_id}/replan")
async def replan_unarchived(job_id: str, archive=Depends(_require_archive)):
    try:
        return await archive.replan(job_id)
    except ValueError as exc:
        raise _bad_request(str(exc), "replan_invalid") from exc


@router.post("/skipped/{job_id}/assign")
async def assign_skipped(
    job_id: str, body: ReassignRequest, archive=Depends(_require_archive)
):
    try:
        return await archive.assign(job_id, body.directory)
    except ValueError as exc:
        raise _bad_request(str(exc), "assign_invalid") from exc
    except Exception as exc:
        logger.exception("手动归档 %s 失败", job_id)
        raise HTTPException(
            503,
            {
                "code": "archive_execute_unavailable",
                "message": "执行归档操作失败",
                "retryable": True,
            },
        ) from exc


@router.get("/operations")
async def list_operations(archive=Depends(_require_archive)):
    return {"items": await archive.list_operations()}


@router.post("/operations/{operation_id}/undo")
async def undo_operation(operation_id: str, archive=Depends(_require_archive)):
    from src.engine.components.archive.journal import UndoConflict

    try:
        return await archive.undo(operation_id)
    except UndoConflict as exc:
        raise HTTPException(
            409,
            {
                "code": "undo_conflict",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except ValueError as exc:
        raise _bad_request(str(exc), "operation_invalid") from exc


@router.post("/operations/{operation_id}/reindex")
async def reindex_operation(operation_id: str, archive=Depends(_require_archive)):
    try:
        return await archive.reindex(operation_id)
    except ValueError as exc:
        raise _bad_request(str(exc), "reindex_invalid") from exc


@router.get("/mode")
async def get_mode(archive=Depends(_require_archive)):
    return archive.mode()


@router.put("/mode")
async def set_mode(body: ModeRequest, archive=Depends(_require_archive)):
    return archive.set_mode(body.review_all)


@router.get("/tree")
async def get_tree(archive=Depends(_require_archive)):
    return {"items": await archive.tree()}


@router.get("/policy")
@router.get("/policies")
async def get_policy(archive=Depends(_require_archive)):
    return await archive.get_policy()


@router.put("/policy")
@router.put("/policies")
async def update_policy(body: PolicyRequest, archive=Depends(_require_archive)):
    try:
        return await archive.update_policy(enabled=body.enabled, rules=body.rules)
    except ValueError as exc:
        raise _bad_request(str(exc), "policy_invalid") from exc


@router.get("/legacy/scan")
async def scan_legacy(archive=Depends(_require_archive)):
    return {"items": await archive.scan_legacy()}


@router.post("/legacy/plan")
async def plan_legacy(
    body: LegacyPlanRequest, archive=Depends(_require_archive)
):
    try:
        return await archive.plan_legacy(body.document_ids)
    except ValueError as exc:
        raise _bad_request(str(exc), "legacy_plan_invalid") from exc
    except Exception as exc:
        logger.exception("生成存量归档预览失败")
        raise HTTPException(
            503,
            {
                "code": "archive_plan_unavailable",
                "message": "暂时无法生成归档预览，请稍后重试",
                "retryable": True,
            },
        ) from exc


@router.post("/legacy/execute")
async def execute_legacy(
    body: LegacyExecuteRequest, archive=Depends(_require_archive)
):
    try:
        return await archive.execute_legacy(
            body.batch_id, body.document_ids, body.overrides
        )
    except ValueError as exc:
        raise _bad_request(str(exc), "legacy_execute_invalid") from exc


@router.post("/inbox")
async def upload_to_inbox(
    file: UploadFile = File(...), archive=Depends(_require_archive)
):
    """Web 上传入口：文件落入 inbox，由归档流水线接管。"""
    if not file.filename:
        raise _bad_request("未读取到文件名", "missing_filename")
    extension = Path(file.filename).suffix.lower()
    if extension not in SUPPORTED_INBOX_EXTENSIONS:
        raise _bad_request(
            f"不支持 {extension or '无扩展名'} 文件", "unsupported_file_type"
        )
    data = await file.read()
    if not data:
        raise _bad_request("文件内容为空", "empty_file")

    inbox = Path(archive.config.inbox_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    # 文件名坍缩为单组件，防穿越（与文档上传同语义）。
    safe_name = Path(file.filename).name
    if safe_name in ("", ".", ".."):
        safe_name = "document"
    target = inbox / safe_name
    if target.exists():
        raise _bad_request(
            f"inbox 已有同名文件: {safe_name}", "inbox_name_conflict"
        )
    target.write_bytes(data)

    # 立即触发一次扫描（否则要等下个轮询周期）。
    scan = await archive.scan_now()
    return {
        "filename": safe_name,
        "queued": safe_name in scan.enqueued or scan.skipped_duplicate > 0,
        "enqueued": scan.enqueued,
    }
