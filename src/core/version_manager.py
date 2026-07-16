"""文档版本管理：版本查询、diff 计算、回滚。"""

from __future__ import annotations

import asyncio
import difflib
import logging
import time
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update

from src.db.models import Document, DocumentVersion
from src.db.postgres import async_session_factory

logger = logging.getLogger(__name__)

# 保持后台任务引用，防止被 GC 回收
_background_tasks: set[asyncio.Task] = set()


class VersionManager:
    """文档版本管理。"""

    def __init__(self, pipeline=None) -> None:
        """初始化版本管理器。

        Args:
            pipeline: Pipeline 实例，用于回滚时触发 re-index。
        """
        self._pipeline = pipeline

    async def list_versions(self, doc_id: UUID) -> dict[str, Any]:
        """获取文档的版本列表（按 version DESC）。"""
        t0 = time.monotonic()
        async with async_session_factory() as session:
            # 获取当前版本号
            doc = await session.get(Document, doc_id)
            if not doc:
                return {"doc_id": str(doc_id), "current_version": 0, "versions": []}

            result = await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.doc_id == doc_id)
                .order_by(DocumentVersion.version.desc())
            )
            versions = result.scalars().all()
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"版本列表查询: doc_id={doc_id} | "
                f"{len(versions)} 个版本 | 耗时 {elapsed_ms:.0f}ms"
            )

            return {
                "doc_id": str(doc_id),
                "current_version": versions[0].version if versions else 0,
                "versions": [
                    {
                        "version": v.version,
                        "change_type": v.change_type,
                        "change_summary": v.change_summary,
                        "content_hash": v.content_hash,
                        "created_at": v.created_at.isoformat() if v.created_at else None,
                    }
                    for v in versions
                ],
            }

    async def get_version(self, doc_id: UUID, version: int) -> dict[str, Any] | None:
        """获取指定版本的详细内容。"""
        t0 = time.monotonic()
        async with async_session_factory() as session:
            result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.doc_id == doc_id,
                    DocumentVersion.version == version,
                )
            )
            v = result.scalar_one_or_none()
            if not v:
                return None
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"版本详情查询: doc_id={doc_id} | v{version} | "
                f"raw_text={len(v.raw_text)}字符 | 耗时 {elapsed_ms:.0f}ms"
            )

            return {
                "version": v.version,
                "raw_text": v.raw_text,
                "content_hash": v.content_hash,
                "file_path": v.file_path,
                "change_type": v.change_type,
                "change_summary": v.change_summary,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }

    async def get_version_file(self, doc_id: UUID, version: int) -> str | None:
        """返回版本快照文件路径（供 FileResponse 使用）。"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(DocumentVersion.file_path).where(
                    DocumentVersion.doc_id == doc_id,
                    DocumentVersion.version == version,
                )
            )
            file_path = result.scalar_one_or_none()
            if file_path and Path(file_path).exists():
                return file_path
            return None

    async def get_diff(
        self, doc_id: UUID, from_version: int, to_version: int
    ) -> dict[str, Any] | None:
        """计算两个版本之间的 unified diff。"""
        t0 = time.monotonic()
        async with async_session_factory() as session:
            result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.doc_id == doc_id,
                    DocumentVersion.version.in_([from_version, to_version]),  # type: ignore[union-attr]
                )
            )
            versions = {v.version: v for v in result.scalars().all()}

            if from_version not in versions or to_version not in versions:
                return None

            old_text = versions[from_version].raw_text
            new_text = versions[to_version].raw_text

            diff_text, stats = compute_unified_diff(
                old_text, new_text,
                f"v{from_version}",
                f"v{to_version}",
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"版本 diff 计算: doc_id={doc_id} | v{from_version}→v{to_version} | "
                f"+{stats['added']}/-{stats['removed']} 行 | 耗时 {elapsed_ms:.0f}ms"
            )

            return {
                "from_version": from_version,
                "to_version": to_version,
                "diff": diff_text,
                "stats": stats,
            }

    async def rollback(self, doc_id: UUID, target_version: int) -> dict[str, Any] | None:
        """回滚到指定版本：创建新版本（内容等于目标版本），触发 re-index。"""
        trace_id = uuid.uuid4().hex[:8]
        t0 = time.monotonic()
        logger.info(
            f"[trace={trace_id}] 版本回滚开始: doc_id={doc_id} | 目标 v{target_version}"
        )
        async with async_session_factory() as session:
            # 获取目标版本
            result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.doc_id == doc_id,
                    DocumentVersion.version == target_version,
                )
            )
            target = result.scalar_one_or_none()
            if not target:
                logger.warning(
                    f"[trace={trace_id}] 回滚失败: doc_id={doc_id} | "
                    f"目标版本 v{target_version} 不存在"
                )
                return None

            # 获取当前最大版本号
            result = await session.execute(
                select(func.max(DocumentVersion.version)).where(DocumentVersion.doc_id == doc_id)
            )
            max_version = result.scalar() or 0
            new_version = max_version + 1

            # 创建新版本记录
            version = DocumentVersion(
                doc_id=doc_id,
                version=new_version,
                raw_text=target.raw_text,
                content_hash=target.content_hash,
                file_path=target.file_path,
                change_type="rollback",
                change_summary=f"回滚到 v{target_version}",
            )
            session.add(version)

            # 更新文档状态为 stale
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(
                    raw_text=target.raw_text,
                    content_hash=target.content_hash,
                    index_status="stale",
                )
            )
            await session.commit()

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"[trace={trace_id}] 版本回滚完成: doc_id={doc_id} | "
                f"v{target_version} → v{new_version} | "
                f"raw_text={len(target.raw_text)}字符 | DB耗时 {elapsed_ms:.0f}ms"
            )

            # 异步触发 re-index
            if self._pipeline:
                task = asyncio.create_task(
                    self._pipeline.reindex_document(doc_id, target.raw_text)
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)

            return {
                "new_version": new_version,
                "rolled_back_from": max_version,
                "rolled_back_to_content_of": target_version,
                "index_status": "processing",
            }


def compute_unified_diff(
    old_text: str,
    new_text: str,
    old_label: str = "旧版本",
    new_label: str = "新版本",
) -> tuple[str, dict[str, int]]:
    """计算 unified diff，返回 (diff_text, stats)。"""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=old_label, tofile=new_label,
    ))

    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))

    return "".join(diff_lines), {"added": added, "removed": removed}
