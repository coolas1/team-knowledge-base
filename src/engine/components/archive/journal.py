"""操作日志撤销：hash 校验 -> 反向 move -> 更新知识文档路径。

撤销语义：
- destination 当前 sha256 必须等于执行时记录的 hash，否则拒绝并标记 conflict
- 反向 move 回 source（source 被占用则拒绝）
- 关联的 KB 文档保留，只把 documents.file_path 改回 inbox 路径
- 原 job 置回 unarchived（reason=undone），防止扫描器重新入队形成循环
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path
from uuid import UUID

from src.engine.components.store.models import ArchiveJob, ArchiveOperation, Document
from src.engine.components.store.postgres import async_session_factory
from src.engine.interface import KnowledgeBase

logger = logging.getLogger(__name__)


class UndoConflict(Exception):
    """撤销冲突（文件被修改/源位置被占用），拒绝执行。"""


class ArchiveJournal:
    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb

    async def undo(self, operation_id: str) -> dict:
        async with async_session_factory() as session:
            op = await session.get(ArchiveOperation, UUID(operation_id))
            if op is None:
                raise ValueError(f"操作不存在: {operation_id}")
            if op.undo_status == "undone":
                raise ValueError("操作已撤销")
            dest = Path(op.destination_path)
            source = Path(op.source_path)

            # 1. 一致性指纹：归档后文件被外部修改则拒绝
            if not dest.is_file():
                await self._mark(session, op, "conflict")
                raise UndoConflict(f"归档文件不存在: {dest}")
            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if digest != op.content_hash:
                await self._mark(session, op, "conflict")
                raise UndoConflict("归档后文件已被修改，拒绝撤销")
            if source.exists():
                await self._mark(session, op, "conflict")
                raise UndoConflict(f"原位置已被占用: {source}")

        # 2. 反向 move
        await asyncio.to_thread(
            shutil.move, str(dest), str(source)
        )
        logger.info("归档撤销: %s -> %s", dest, source)

        # 3. 保留关联知识，只更新其持久文件位置。
        knowledge_retained = op.kb_doc_id is not None
        if op.kb_doc_id is not None:
            async with async_session_factory() as session:
                doc = await session.get(Document, op.kb_doc_id)
                if doc is not None:
                    doc.file_path = str(source)
                    doc.title = source.name
                    await session.commit()

        # 4. 标记 + 原 job 置回 skipped（防扫描循环）
        async with async_session_factory() as session:
            op2 = await session.get(ArchiveOperation, UUID(operation_id))
            assert op2 is not None
            op2.undo_status = "undone"
            await session.commit()

            if op2.job_id is not None:
                job = await session.get(ArchiveJob, op2.job_id)
                if job is not None:
                    job.status = "unarchived"
                    job.routing_reason = "undone"
                    await session.commit()

        return {
            "operation_id": operation_id,
            "restored_to": str(source),
            "knowledge_retained": knowledge_retained,
            # 兼容旧客户端字段；V2 永远不因撤销归档而删除知识。
            "kb_document_removed": False,
            "undo_status": "undone",
        }

    @staticmethod
    async def _mark(session, op: ArchiveOperation, undo_status: str) -> None:
        op.undo_status = undo_status
        await session.commit()
