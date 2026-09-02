"""Durable queue and source records for conversation-memory retention."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from src.engine.components.store.models import Document

from .models import ConversationMemorySource
from .types import ConversationMemoryJob, ConversationMemoryQueueStats

SessionFactory = Callable[[], Any]

CONVERSATION_DOCUMENT_NAMESPACE = uuid.UUID("c8d8ce89-fb34-56c6-bd60-cd39dfc05e4c")
CONVERSATION_FILE_TYPE = "conversation"
_QUEUE_STATUSES = ("pending", "processing", "completed", "failed", "cancelled")


def conversation_document_id(session_id: str, turn_id: str) -> uuid.UUID:
    session_id = session_id.strip()
    turn_id = turn_id.strip()
    if not session_id or not turn_id:
        raise ValueError("session_id and turn_id must not be empty")
    return uuid.uuid5(CONVERSATION_DOCUMENT_NAMESPACE, f"{session_id}\0{turn_id}")


class PostgresConversationMemoryQueue:
    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        title: str | None = None,
    ) -> ConversationMemoryJob:
        content = content.strip()
        if not content:
            raise ValueError("conversation content must not be empty")
        document_id = conversation_document_id(session_id, turn_id)
        now = datetime.now(timezone.utc)
        display_title = (
            title.strip() if title and title.strip() else "Conversation turn"
        )

        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    insert(Document)
                    .values(
                        id=document_id,
                        title=display_title,
                        file_type=CONVERSATION_FILE_TYPE,
                        raw_text=content,
                        overview="",
                        file_path=None,
                        content_hash=None,
                        status="indexed",
                        error_msg=None,
                    )
                    .on_conflict_do_update(
                        index_elements=[Document.id],
                        set_={
                            "title": display_title,
                            "raw_text": content,
                            "updated_at": func.now(),
                        },
                    )
                )
                await session.execute(
                    insert(ConversationMemorySource)
                    .values(
                        document_id=document_id,
                        session_id=session_id,
                        turn_id=turn_id,
                        status="pending",
                        available_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=[ConversationMemorySource.document_id],
                        set_={
                            "status": case(
                                (
                                    ConversationMemorySource.status == "failed",
                                    "pending",
                                ),
                                else_=ConversationMemorySource.status,
                            ),
                            "attempts": case(
                                (ConversationMemorySource.status == "failed", 0),
                                else_=ConversationMemorySource.attempts,
                            ),
                            "error_msg": case(
                                (ConversationMemorySource.status == "failed", None),
                                else_=ConversationMemorySource.error_msg,
                            ),
                            "available_at": case(
                                (ConversationMemorySource.status == "failed", now),
                                else_=ConversationMemorySource.available_at,
                            ),
                            "updated_at": func.now(),
                        },
                    )
                )
                row = (
                    await session.execute(
                        select(ConversationMemorySource, Document)
                        .join(
                            Document,
                            Document.id == ConversationMemorySource.document_id,
                        )
                        .where(ConversationMemorySource.document_id == document_id)
                    )
                ).one()
        return self._job_from_row(*row)

    async def claim(
        self,
        *,
        limit: int = 1,
        lease_seconds: int = 300,
        max_attempts: int = 10,
        now: datetime | None = None,
    ) -> list[ConversationMemoryJob]:
        if limit < 1 or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("queue limits must be greater than zero")
        now = now or datetime.now(timezone.utc)
        expired_before = now - timedelta(seconds=lease_seconds)
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(ConversationMemorySource, Document)
                        .join(
                            Document,
                            Document.id == ConversationMemorySource.document_id,
                        )
                        .where(
                            ConversationMemorySource.attempts < max_attempts,
                            or_(
                                (
                                    (ConversationMemorySource.status == "pending")
                                    & (ConversationMemorySource.available_at <= now)
                                ),
                                (
                                    (ConversationMemorySource.status == "processing")
                                    & (
                                        ConversationMemorySource.locked_at
                                        < expired_before
                                    )
                                ),
                            ),
                        )
                        .order_by(
                            ConversationMemorySource.available_at,
                            ConversationMemorySource.document_id,
                        )
                        .with_for_update(skip_locked=True)
                        .limit(limit)
                    )
                ).all()
                jobs = []
                for source, document in rows:
                    source.status = "processing"
                    source.locked_at = now
                    source.attempts += 1
                    jobs.append(self._job_from_row(source, document))
                await session.flush()
        return jobs

    async def complete(self, document_id: str) -> bool:
        result = await self._set_terminal_state(document_id, "completed")
        return result

    async def fail(
        self,
        document_id: str,
        error_msg: str,
        *,
        max_attempts: int = 10,
        retry_delay_seconds: float = 1.0,
        now: datetime | None = None,
    ) -> str:
        if max_attempts < 1 or retry_delay_seconds < 0:
            raise ValueError("retry settings are invalid")
        uid = uuid.UUID(document_id)
        now = now or datetime.now(timezone.utc)
        async with self._session_factory() as session:
            async with session.begin():
                source = await session.scalar(
                    select(ConversationMemorySource)
                    .where(ConversationMemorySource.document_id == uid)
                    .with_for_update()
                )
                if source is None:
                    raise ValueError(
                        f"conversation memory source does not exist: {document_id}"
                    )
                if source.status == "cancelled":
                    return "cancelled"
                source.error_msg = error_msg[:2000]
                source.locked_at = None
                if source.attempts >= max_attempts:
                    source.status = "failed"
                else:
                    source.status = "pending"
                    source.available_at = now + timedelta(seconds=retry_delay_seconds)
                return source.status

    async def cancel_session(self, session_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.session_id == session_id,
                    ConversationMemorySource.status.in_(("pending", "processing")),
                )
                .values(status="cancelled", locked_at=None, updated_at=func.now())
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def status_counts(self) -> ConversationMemoryQueueStats:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        ConversationMemorySource.status,
                        func.count(ConversationMemorySource.document_id),
                    ).group_by(ConversationMemorySource.status)
                )
            ).all()
        counts = {status: 0 for status in _QUEUE_STATUSES}
        counts.update({str(status): int(count) for status, count in rows})
        return ConversationMemoryQueueStats(**counts)

    async def get_status(self, document_id: str) -> str | None:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            return await session.scalar(
                select(ConversationMemorySource.status).where(
                    ConversationMemorySource.document_id == uid
                )
            )

    async def _set_terminal_state(self, document_id: str, status: str) -> bool:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.document_id == uid,
                    ConversationMemorySource.status == "processing",
                )
                .values(
                    status=status,
                    error_msg=None,
                    locked_at=None,
                    updated_at=func.now(),
                )
            )
            await session.commit()
        return bool(result.rowcount)

    @staticmethod
    def _job_from_row(
        source: ConversationMemorySource, document: Document
    ) -> ConversationMemoryJob:
        return ConversationMemoryJob(
            document_id=str(source.document_id),
            session_id=source.session_id,
            turn_id=source.turn_id,
            title=document.title,
            content=document.raw_text,
            attempts=source.attempts,
            status=source.status,
        )
