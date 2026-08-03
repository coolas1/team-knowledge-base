"""Durable PostgreSQL outbox and worker for the Neo4j memory projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from .graph_projector import MemoryGraphProjector
from .graph_types import MemoryGraphProjection
from .models import HindsightGraphOutbox


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class GraphOutboxEvent:
    id: int
    document_id: str
    operation: str
    attempts: int


@dataclass(frozen=True, slots=True)
class GraphProjectionRun:
    event_id: int
    document_id: str
    operation: str
    status: str
    error: str | None = None


class GraphOutbox(Protocol):
    async def claim(
        self,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 10,
    ) -> GraphOutboxEvent | None: ...

    async def complete(self, event_id: int) -> None: ...

    async def fail(
        self,
        event_id: int,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> None: ...


class GraphProjectionSource(Protocol):
    async def graph_projection(
        self, document_id: str
    ) -> MemoryGraphProjection | None: ...


class PostgresGraphOutbox:
    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def claim(
        self,
        *,
        lease_seconds: int = 300,
        max_attempts: int = 10,
    ) -> GraphOutboxEvent | None:
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("lease_seconds and max_attempts must be positive")
        now = _utcnow()
        stale_before = now - timedelta(seconds=lease_seconds)
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    self._claim_statement(now, stale_before, max_attempts)
                )
                if row is None:
                    return None
                row.status = "processing"
                row.attempts += 1
                row.locked_at = now
                row.error_msg = None
                row.updated_at = now
                await session.flush()
                return self._event_from_row(row)

    @staticmethod
    def _claim_statement(
        now: datetime,
        stale_before: datetime,
        max_attempts: int,
    ):
        earlier = aliased(HindsightGraphOutbox)
        earlier_unfinished = (
            select(earlier.id)
            .where(
                earlier.document_id == HindsightGraphOutbox.document_id,
                earlier.id < HindsightGraphOutbox.id,
                earlier.status != "completed",
                earlier.attempts < max_attempts,
            )
            .exists()
        )
        return (
            select(HindsightGraphOutbox)
            .where(
                HindsightGraphOutbox.attempts < max_attempts,
                HindsightGraphOutbox.available_at <= now,
                or_(
                    HindsightGraphOutbox.status.in_(("pending", "failed")),
                    and_(
                        HindsightGraphOutbox.status == "processing",
                        HindsightGraphOutbox.locked_at < stale_before,
                    ),
                ),
                ~earlier_unfinished,
            )
            .order_by(HindsightGraphOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    async def complete(self, event_id: int) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(HindsightGraphOutbox, event_id)
                if row is None:
                    return
                row.status = "completed"
                row.locked_at = None
                row.error_msg = None
                row.updated_at = _utcnow()

    async def fail(
        self,
        event_id: int,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> None:
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        now = _utcnow()
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(HindsightGraphOutbox, event_id)
                if row is None:
                    return
                row.status = "failed"
                row.locked_at = None
                row.error_msg = error[:4000]
                row.available_at = now + timedelta(seconds=retry_delay_seconds)
                row.updated_at = now

    @staticmethod
    def _event_from_row(row: HindsightGraphOutbox) -> GraphOutboxEvent:
        return GraphOutboxEvent(
            id=row.id,
            document_id=str(row.document_id),
            operation=row.operation,
            attempts=row.attempts,
        )


class GraphProjectionWorker:
    def __init__(
        self,
        outbox: GraphOutbox,
        source: GraphProjectionSource,
        projector: MemoryGraphProjector,
        *,
        max_attempts: int = 10,
        lease_seconds: int = 300,
    ) -> None:
        if max_attempts < 1 or lease_seconds < 1:
            raise ValueError("max_attempts and lease_seconds must be positive")
        self._outbox = outbox
        self._source = source
        self._projector = projector
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds

    async def run_once(self) -> GraphProjectionRun | None:
        event = await self._outbox.claim(
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        if event is None:
            return None
        try:
            if event.operation == "delete":
                await self._projector.delete_document(event.document_id)
            elif event.operation == "replace":
                projection = await self._source.graph_projection(event.document_id)
                if projection is None:
                    # The document may have been deleted after this replace event.
                    await self._projector.delete_document(event.document_id)
                else:
                    await self._projector.replace_document(projection)
            else:
                raise ValueError(f"unsupported graph operation: {event.operation}")
        except Exception as error:
            delay = min(2 ** min(event.attempts, 8), 300)
            await self._outbox.fail(
                event.id,
                str(error),
                retry_delay_seconds=delay,
            )
            return GraphProjectionRun(
                event_id=event.id,
                document_id=event.document_id,
                operation=event.operation,
                status="failed",
                error=str(error),
            )

        await self._outbox.complete(event.id)
        return GraphProjectionRun(
            event_id=event.id,
            document_id=event.document_id,
            operation=event.operation,
            status="completed",
        )

    async def drain(self, *, limit: int = 100) -> list[GraphProjectionRun]:
        if limit < 1:
            raise ValueError("limit must be positive")
        results: list[GraphProjectionRun] = []
        for _ in range(limit):
            result = await self.run_once()
            if result is None:
                break
            results.append(result)
        return results
