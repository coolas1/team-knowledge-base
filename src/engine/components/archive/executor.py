"""归档执行器：move -> 操作日志 -> 知识库入库（design.md D5）。

入库联动：kb.ingest(IngestSource(path=归档路径, keep_path=True))，
不复制字节、documents.file_path 直接指向归档位置。
入库失败不回滚 move：operation 记 indexing_failed，可通过 reindex 重试。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from src.engine.components.store.models import ArchiveOperation, Document
from src.engine.components.store.postgres import async_session_factory
from src.engine.interface import IngestSource, KnowledgeBase

from .jobs import ArchiveJobQueue, ClaimedJob
from .planner import ActionPlan

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExecutionResult:
    operation_id: str
    destination_path: str
    new_name: str
    kb_doc_id: str | None
    indexing_failed: bool
    error_msg: str | None = None


class ArchiveExecutor:
    def __init__(self, kb: KnowledgeBase, queue: ArchiveJobQueue) -> None:
        self._kb = kb
        self._queue = queue

    async def prepare_knowledge(self, job: ClaimedJob, extracted_text: str) -> str | None:
        """在文件仍位于 inbox 时启动知识库流水线，并复用已提取全文。"""
        ref = await self._kb.ingest(
            IngestSource(
                name=job.file_name,
                path=Path(job.file_path),
                keep_path=True,
                extracted_text=extracted_text,
            )
        )
        return ref.id or None

    async def execute(
        self,
        job: ClaimedJob,
        plan: ActionPlan,
        decision_source: str,
        *,
        kb_doc_id: str | None = None,
        extracted_text: str | None = None,
        policy_version: int | None = None,
        policy_id: str | None = None,
    ) -> ExecutionResult:
        """执行一个已校验的 plan。decision_source: auto | review | manual。"""
        # 1. 建目录（新目录提议）
        if plan.creates_directory:
            plan.destination_dir.mkdir(parents=True, exist_ok=True)

        # 2. move（同卷 rename 语义，to_thread 隔离阻塞 IO）
        await asyncio.to_thread(
            shutil.move, str(plan.source_path), str(plan.destination_path)
        )
        logger.info(
            "归档执行: %s -> %s (%s)",
            plan.source_path,
            plan.destination_path,
            decision_source,
        )

        # 3. 操作日志（撤销依据）
        async with async_session_factory() as session:
            operation = ArchiveOperation(
                job_id=UUID(job.id),
                source_path=str(plan.source_path),
                destination_path=str(plan.destination_path),
                content_hash=plan.content_hash,
                decision_source=decision_source,
                confidence=plan.decision.confidence,
                rationale=plan.decision.rationale,
                policy_version=policy_version,
                policy_id=UUID(policy_id) if policy_id else None,
                status="executed",
            )
            session.add(operation)
            await session.commit()
            operation_id = str(operation.id)

        # 4. 知识库入库（失败不回滚 move）
        indexing_failed = False
        error_msg: str | None = None
        try:
            if kb_doc_id:
                # prepare_knowledge 已创建文档并启动异步知识分析；move 后只需
                # 把持久路径切到 archive，不能重复 ingest。
                async with async_session_factory() as session:
                    doc = await session.get(Document, UUID(kb_doc_id))
                    if doc is None:
                        raise ValueError(f"知识库文档不存在: {kb_doc_id}")
                    doc.file_path = str(plan.destination_path)
                    doc.title = plan.new_name
                    await session.commit()
            else:
                ref = await self._kb.ingest(
                    IngestSource(
                        name=plan.new_name,
                        path=plan.destination_path,
                        keep_path=True,
                        extracted_text=extracted_text,
                    )
                )
                kb_doc_id = ref.id or None
        except Exception as exc:
            indexing_failed = True
            error_msg = str(exc)
            logger.exception("归档后入库失败: %s", plan.destination_path)

        async with async_session_factory() as session:
            op = await session.get(ArchiveOperation, UUID(operation_id))
            assert op is not None
            op.kb_doc_id = UUID(kb_doc_id) if kb_doc_id else None
            op.status = "indexing_failed" if indexing_failed else "done"
            op.error_msg = error_msg
            await session.commit()

        # 5. job 终态
        await self._queue.set_status(
            job.id,
            "done",
            error_msg=f"入库失败: {error_msg}" if indexing_failed else None,
        )
        return ExecutionResult(
            operation_id=operation_id,
            destination_path=str(plan.destination_path),
            new_name=plan.new_name,
            kb_doc_id=kb_doc_id,
            indexing_failed=indexing_failed,
            error_msg=error_msg,
        )

    async def reindex(self, operation_id: str) -> dict:
        """重试入库失败的操作（move 不重做，只重新 ingest）。"""
        async with async_session_factory() as session:
            op = await session.get(ArchiveOperation, UUID(operation_id))
            if op is None:
                raise ValueError(f"操作不存在: {operation_id}")
            if op.undo_status == "undone":
                raise ValueError("操作已撤销，无需重试入库")
            dest = Path(op.destination_path)
            if not dest.is_file():
                raise ValueError(f"归档文件不存在: {dest}")

        try:
            ref = await self._kb.ingest(
                IngestSource(name=dest.name, path=dest, keep_path=True)
            )
        except Exception as exc:
            async with async_session_factory() as session:
                op = await session.get(ArchiveOperation, UUID(operation_id))
                assert op is not None
                op.error_msg = str(exc)
                await session.commit()
            raise ValueError(f"重试入库失败: {exc}") from exc

        async with async_session_factory() as session:
            op = await session.get(ArchiveOperation, UUID(operation_id))
            assert op is not None
            op.kb_doc_id = UUID(ref.id) if ref.id else None
            op.status = "done"
            op.error_msg = None
            await session.commit()
        return {"operation_id": operation_id, "kb_doc_id": ref.id, "status": "done"}

    @staticmethod
    async def load_operation(operation_id: str) -> ArchiveOperation | None:
        async with async_session_factory() as session:
            return await session.get(ArchiveOperation, UUID(operation_id))

    @staticmethod
    async def find_by_job(job_id: str) -> ArchiveOperation | None:
        async with async_session_factory() as session:
            return await session.scalar(
                select(ArchiveOperation)
                .where(ArchiveOperation.job_id == UUID(job_id))
                .order_by(ArchiveOperation.created_at.desc())
                .limit(1)
            )
