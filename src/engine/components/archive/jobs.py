"""归档持久队列：Postgres 单表 claim/lease/retry（仿 graph_outbox 语义）。

与 HindsightGraphOutbox 的差异：无 per-document 顺序约束（归档 job 相互
独立），其余语义一致 —— FOR UPDATE SKIP LOCKED 抢占、lease 过期回收、
attempts + available_at 指数退避、超限转 dead。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, or_, select

from src.engine.components.store.models import ArchiveJob


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """claim 成功后交给 worker 的纯数据（避免跨 session 持有 ORM 对象）。"""

    id: str
    file_name: str
    file_path: str
    content_hash: str
    attempts: int
    plan: dict


def to_claimed(row: ArchiveJob) -> ClaimedJob:
    """ORM 行 -> ClaimedJob（worker/人审执行共用）。"""
    return ClaimedJob(
        id=str(row.id),
        file_name=row.file_name,
        file_path=row.file_path,
        content_hash=row.content_hash,
        attempts=row.attempts,
        plan=row.plan or {},
    )


class ArchiveJobQueue(Protocol):
    async def claim(
        self, *, lease_seconds: int = 300, max_attempts: int = 5
    ) -> ClaimedJob | None: ...

    async def fail(
        self, job_id: str, attempt: int, error: str, *, retry_delay_seconds: int
    ) -> bool: ...

    async def set_status(
        self,
        job_id: str,
        status: str,
        *,
        plan: dict | None = None,
        routing_reason: str | None = None,
        error_msg: str | None = None,
    ) -> ArchiveJob | None: ...


class PostgresArchiveJobQueue:
    """archive_jobs 表上的队列原语。"""

    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def enqueue(
        self, *, file_name: str, file_path: str, content_hash: str
    ) -> ClaimedJob | None:
        """发现稳定文件时入队；同 content_hash 已存在则跳过（返回 None）。"""
        async with self._session_factory() as session:
            async with session.begin():
                exists = await session.scalar(
                    select(ArchiveJob.id).where(
                        ArchiveJob.content_hash == content_hash
                    )
                )
                if exists is not None:
                    return None
                job = ArchiveJob(
                    file_name=file_name,
                    file_path=file_path,
                    content_hash=content_hash,
                    status="queued",
                )
                session.add(job)
                await session.flush()
                return ClaimedJob(
                    id=str(job.id),
                    file_name=file_name,
                    file_path=file_path,
                    content_hash=content_hash,
                    attempts=0,
                    plan={},
                )

    async def claim(
        self, *, lease_seconds: int = 300, max_attempts: int = 5
    ) -> ClaimedJob | None:
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("lease_seconds and max_attempts must be positive")
        now = _utcnow()
        stale_before = now - timedelta(seconds=lease_seconds)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(ArchiveJob)
                    .where(
                        ArchiveJob.attempts < max_attempts,
                        ArchiveJob.available_at <= now,
                        or_(
                            ArchiveJob.status.in_(("queued", "failed")),
                            and_(
                                ArchiveJob.status == "processing",
                                ArchiveJob.locked_at < stale_before,
                            ),
                        ),
                    )
                    .order_by(ArchiveJob.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                if row is None:
                    return None
                row.status = "processing"
                row.attempts += 1
                row.locked_at = now
                row.error_msg = None
                await session.flush()
                return to_claimed(row)

    async def fail(
        self, job_id: str, attempt: int, error: str, *, retry_delay_seconds: int
    ) -> bool:
        """记失败 + 退避；attempts 超限时由调用方（worker）转 dead。"""
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        now = _utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._owned_job_statement(job_id, attempt)
                )
                if row is None:
                    return False
                row.status = "failed"
                row.locked_at = None
                row.error_msg = error[:4000]
                row.available_at = now + timedelta(seconds=retry_delay_seconds)
                return True

    async def mark_dead(self, job_id: str, attempt: int, error: str) -> bool:
        """超过 max_attempts 的终态：不再重试，留在列表中可见。"""
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._owned_job_statement(job_id, attempt)
                )
                if row is None:
                    return False
                row.status = "dead"
                row.locked_at = None
                row.error_msg = error[:4000]
                return True

    async def set_status(
        self,
        job_id: str,
        status: str,
        *,
        plan: dict | None = None,
        routing_reason: str | None = None,
        error_msg: str | None = None,
    ) -> ArchiveJob | None:
        """worker/人审推进状态：awaiting_review / skipped / done 等。

        返回更新后的 ORM 行（供调用方读取）。并发人审重复批准由
        planner 的源文件存在性/指纹校验兜底（第二次执行时源已不在）。
        """
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(
                    ArchiveJob, UUID(job_id) if isinstance(job_id, str) else job_id
                )
                if row is None:
                    return None
                row.status = status
                if plan is not None:
                    row.plan = plan
                if routing_reason is not None:
                    row.routing_reason = routing_reason
                if error_msg is not None:
                    row.error_msg = error_msg
                else:
                    row.error_msg = None
                row.locked_at = None
                return row

    @staticmethod
    def _owned_job_statement(job_id: str, attempt: int):
        return (
            select(ArchiveJob)
            .where(
                ArchiveJob.id == UUID(job_id),
                ArchiveJob.status == "processing",
                ArchiveJob.attempts == attempt,
            )
            .with_for_update()
        )
