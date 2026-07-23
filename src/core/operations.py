"""PostgreSQL-backed durable operation queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.config import settings
from src.db.models import Operation
from src.db.postgres import async_session_factory
from src.pipeline.pipeline import Pipeline

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyConflict(ValueError):
    pass


class OperationManager:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
        self._worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="tkb-operation-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        team_id: str,
        operation_type: str,
        payload: dict[str, Any],
        document_id: uuid.UUID | None,
        idempotency_key: str | None = None,
        hash_payload: dict[str, Any] | None = None,
    ) -> Operation:
        key = idempotency_key or str(uuid.uuid4())
        digest = request_hash(hash_payload or payload)
        existing = await session.scalar(
            select(Operation).where(
                Operation.team_id == team_id,
                Operation.idempotency_key == key,
            )
        )
        if existing:
            if existing.request_hash != digest:
                raise IdempotencyConflict("相同 idempotency key 对应了不同请求")
            return existing

        operation = Operation(
            team_id=team_id,
            document_id=document_id,
            operation_type=operation_type,
            status="pending",
            idempotency_key=key,
            request_hash=digest,
            payload=payload,
            max_attempts=settings.worker_max_attempts,
        )
        session.add(operation)
        await session.flush()
        return operation

    async def find_idempotent(
        self,
        session: AsyncSession,
        *,
        team_id: str,
        idempotency_key: str | None,
        hash_payload: dict[str, Any],
    ) -> Operation | None:
        if not idempotency_key:
            return None
        operation = await session.scalar(
            select(Operation).where(
                Operation.team_id == team_id,
                Operation.idempotency_key == idempotency_key,
            )
        )
        if operation and operation.request_hash != request_hash(hash_payload):
            raise IdempotencyConflict("相同 idempotency key 对应了不同请求")
        return operation

    async def get(
        self, session: AsyncSession, team_id: str, operation_id: uuid.UUID
    ) -> dict[str, Any] | None:
        operation = await session.scalar(
            select(Operation).where(Operation.id == operation_id, Operation.team_id == team_id)
        )
        return self._serialize(operation) if operation else None

    async def list(
        self, session: AsyncSession, team_id: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        stmt = select(Operation).where(Operation.team_id == team_id)
        if status:
            stmt = stmt.where(Operation.status == status)
        rows = (await session.scalars(stmt.order_by(Operation.created_at.desc()).limit(limit))).all()
        return [self._serialize(row) for row in rows]

    async def retry(
        self, session: AsyncSession, team_id: str, operation_id: uuid.UUID
    ) -> dict[str, Any] | None:
        operation = await session.scalar(
            select(Operation).where(Operation.id == operation_id, Operation.team_id == team_id).with_for_update()
        )
        if not operation:
            return None
        if operation.status not in {"failed", "cancelled"}:
            raise ValueError("只有 failed/cancelled operation 可以手动重试")
        operation.status = "pending"
        operation.attempt_count = 0
        operation.next_retry_at = None
        operation.error_message = None
        operation.worker_id = None
        operation.lease_expires_at = None
        await session.commit()
        return self._serialize(operation)

    async def cancel(
        self, session: AsyncSession, team_id: str, operation_id: uuid.UUID
    ) -> dict[str, Any] | None:
        operation = await session.scalar(
            select(Operation).where(Operation.id == operation_id, Operation.team_id == team_id).with_for_update()
        )
        if not operation:
            return None
        if operation.status in {"succeeded", "failed", "cancelled"}:
            return self._serialize(operation)
        if operation.status == "processing":
            raise ValueError("运行中的 Operation 暂不支持强制取消")
        operation.status = "cancelled"
        operation.worker_id = None
        operation.lease_expires_at = None
        await session.commit()
        return self._serialize(operation)

    async def _run(self) -> None:
        while not self._stop.is_set():
            operation = None
            try:
                operation = await self._claim()
                if operation is None:
                    await asyncio.sleep(settings.operation_poll_interval)
                    continue
                await self._execute(operation)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Operation worker loop failed")
                await asyncio.sleep(settings.operation_poll_interval)

    async def _claim(self, team_id: str | None = None) -> dict[str, Any] | None:
        now = _utcnow()
        async with async_session_factory() as session:
            stmt = (
                select(Operation)
                .where(
                    or_(
                        and_(
                            Operation.status.in_(["pending", "retry_wait"]),
                            or_(Operation.next_retry_at.is_(None), Operation.next_retry_at <= now),
                        ),
                        and_(Operation.status == "processing", Operation.lease_expires_at < now),
                    )
                )
                .order_by(Operation.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if team_id:
                stmt = stmt.where(Operation.team_id == team_id)
            operation = await session.scalar(stmt)
            if not operation:
                return None
            operation.status = "processing"
            operation.worker_id = self._worker_id
            operation.lease_expires_at = now + timedelta(seconds=settings.operation_lease_seconds)
            operation.attempt_count += 1
            operation.error_message = None
            await session.commit()
            return {
                "id": operation.id,
                "team_id": operation.team_id,
                "operation_type": operation.operation_type,
                "payload": operation.payload,
                "attempt_count": operation.attempt_count,
                "max_attempts": operation.max_attempts,
            }

    async def _execute(self, operation: dict[str, Any]) -> None:
        lock_session = async_session_factory()
        lock_key = f"{operation['team_id']}:{operation['payload'].get('doc_id', '')}"
        acquired = bool(
            await lock_session.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
        )
        if not acquired:
            await lock_session.close()
            await self._defer(operation, "同一文档已有运行中的 Operation")
            return
        heartbeat = asyncio.create_task(self._heartbeat(operation["id"]))
        try:
            payload = operation["payload"]
            if operation["operation_type"] == "index_document":
                from pathlib import Path

                await self._pipeline.process_file(
                    uuid.UUID(payload["doc_id"]),
                    Path(payload["file_path"]),
                    payload["title"],
                    payload["file_type"],
                    operation["team_id"],
                )
            elif operation["operation_type"] == "reindex_document":
                await self._pipeline.reindex_document(
                    uuid.UUID(payload["doc_id"]), payload["content"], operation["team_id"]
                )
            else:
                raise ValueError(f"未知 operation_type: {operation['operation_type']}")
        except Exception as exc:
            await self._mark_failed_or_retry(operation, exc)
            return
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            await lock_session.close()

        async with async_session_factory() as session:
            row = await session.get(Operation, operation["id"])
            if row and row.status == "processing" and row.worker_id == self._worker_id:
                row.status = "succeeded"
                row.progress = 100
                row.result = {"document_id": str(row.document_id) if row.document_id else None}
                row.worker_id = None
                row.lease_expires_at = None
                await session.commit()

    async def _heartbeat(self, operation_id: uuid.UUID) -> None:
        interval = max(1, settings.operation_lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            async with async_session_factory() as session:
                row = await session.get(Operation, operation_id)
                if not row or row.status != "processing" or row.worker_id != self._worker_id:
                    return
                row.lease_expires_at = _utcnow() + timedelta(seconds=settings.operation_lease_seconds)
                await session.commit()

    async def _defer(self, operation: dict[str, Any], reason: str) -> None:
        async with async_session_factory() as session:
            row = await session.get(Operation, operation["id"])
            if not row:
                return
            row.status = "retry_wait"
            row.next_retry_at = _utcnow() + timedelta(seconds=1)
            row.attempt_count = max(0, row.attempt_count - 1)
            row.error_message = reason
            row.worker_id = None
            row.lease_expires_at = None
            await session.commit()

    async def _mark_failed_or_retry(self, operation: dict[str, Any], exc: Exception) -> None:
        async with async_session_factory() as session:
            row = await session.get(Operation, operation["id"])
            if not row or row.status == "cancelled":
                return
            row.error_message = str(exc)
            row.worker_id = None
            row.lease_expires_at = None
            if row.attempt_count < row.max_attempts:
                row.status = "retry_wait"
                row.next_retry_at = _utcnow() + timedelta(seconds=2 ** row.attempt_count)
            else:
                row.status = "failed"
                row.next_retry_at = None
            await session.commit()

    @staticmethod
    def _serialize(operation: Operation) -> dict[str, Any]:
        return {
            "operation_id": str(operation.id),
            "team_id": operation.team_id,
            "document_id": str(operation.document_id) if operation.document_id else None,
            "operation_type": operation.operation_type,
            "status": operation.status,
            "progress": operation.progress,
            "attempt_count": operation.attempt_count,
            "max_attempts": operation.max_attempts,
            "next_retry_at": operation.next_retry_at.isoformat() if operation.next_retry_at else None,
            "error_message": operation.error_message,
            "result": operation.result,
            "created_at": operation.created_at.isoformat() if operation.created_at else None,
            "updated_at": operation.updated_at.isoformat() if operation.updated_at else None,
        }
