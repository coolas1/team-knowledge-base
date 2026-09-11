"""Durable queue and source records for conversation-memory retention."""

from __future__ import annotations

import uuid
import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from src.engine.components.store.models import Document
from src.engine.components.store.scope import scope_predicate
from src.engine.scope import MemoryScope, DEFAULT_BANK_ID, TagFilter

from .models import ConversationMemorySource
from .types import ConversationMemoryJob, ConversationMemoryQueueStats

SessionFactory = Callable[[], Any]

CONVERSATION_DOCUMENT_NAMESPACE = uuid.UUID("c8d8ce89-fb34-56c6-bd60-cd39dfc05e4c")
CONVERSATION_FILE_TYPE = "conversation"
_QUEUE_STATUSES = ("pending", "processing", "completed", "failed", "cancelled")


def conversation_document_id(
    session_id: str, turn_id: str, bank_id: str = DEFAULT_BANK_ID
) -> uuid.UUID:
    session_id = session_id.strip()
    turn_id = turn_id.strip()
    if not session_id or not turn_id:
        raise ValueError("session_id and turn_id must not be empty")
    namespace = (
        CONVERSATION_DOCUMENT_NAMESPACE
        if bank_id == DEFAULT_BANK_ID
        else uuid.uuid5(CONVERSATION_DOCUMENT_NAMESPACE, bank_id)
    )
    return uuid.uuid5(namespace, f"{session_id}\0{turn_id}")


class PostgresConversationMemoryQueue:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        scope: MemoryScope | None = None,
        write_tags: tuple[str, ...] = (),
        all_banks: bool = False,
    ) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory
        self.scope = scope or MemoryScope()
        self._write_tags = TagFilter(write_tags).tags
        self._all_banks = all_banks

    def with_scope(self, scope: MemoryScope, *, write_tags: tuple[str, ...] = ()):
        return PostgresConversationMemoryQueue(
            self._session_factory, scope=scope, write_tags=write_tags
        )

    def _document_scope(self):
        return scope_predicate(Document.bank_id, Document.tags, self.scope)

    def _source_scope(self):
        return (
            ConversationMemorySource.bank_id == self.scope.bank_id
        ) & ConversationMemorySource.document_id.in_(
            select(Document.id).where(self._document_scope())
        )

    async def enqueue(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        title: str | None = None,
        request_fingerprint: str | None = None,
        source_context: dict | None = None,
    ) -> ConversationMemoryJob:
        content = content.strip()
        if not content:
            raise ValueError("conversation content must not be empty")
        fingerprint = (
            request_fingerprint or hashlib.sha256(content.encode()).hexdigest()
        )
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise ValueError("invalid conversation fingerprint")
        if not self.scope.permits(self.scope.bank_id, self._write_tags):
            raise ValueError("conversation write tags are outside the trusted scope")
        document_id = conversation_document_id(session_id, turn_id, self.scope.bank_id)
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
                        bank_id=self.scope.bank_id,
                        tags=list(self._write_tags),
                        title=display_title,
                        file_type=CONVERSATION_FILE_TYPE,
                        raw_text=content,
                        overview="",
                        file_path=None,
                        content_hash=fingerprint,
                        status="indexed",
                        error_msg=None,
                    )
                    .on_conflict_do_nothing(index_elements=[Document.id])
                )
                owner = (
                    await session.execute(
                        select(Document)
                        .where(Document.id == document_id, self._document_scope())
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if owner is None:
                    raise ValueError("conversation write is outside the trusted scope")
                if (
                    owner.raw_text != content
                    or owner.file_type != CONVERSATION_FILE_TYPE
                    or getattr(owner, "content_hash", None) not in (None, fingerprint)
                ):
                    raise ValueError("conversation_delivery_conflict")
                owner.content_hash = fingerprint
                await session.execute(
                    insert(ConversationMemorySource)
                    .values(
                        document_id=document_id,
                        bank_id=self.scope.bank_id,
                        operation_id=document_id,
                        source_context=source_context or {},
                        stage_results={"delivery": "accepted", "retain": "pending"},
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
                        .where(
                            ConversationMemorySource.document_id == document_id,
                            self._source_scope(),
                        )
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
                await session.execute(
                    update(ConversationMemorySource)
                    .where(
                        True if self._all_banks else self._source_scope(),
                        ConversationMemorySource.status == "processing",
                        ConversationMemorySource.attempts >= max_attempts,
                        or_(
                            ConversationMemorySource.lease_expires_at <= now,
                            ConversationMemorySource.lease_expires_at.is_(None)
                            & (ConversationMemorySource.locked_at < expired_before),
                        ),
                    )
                    .values(
                        status="failed",
                        lease_token=None,
                        lease_expires_at=None,
                        locked_at=None,
                        error_msg="lease_expired",
                        stage_results=ConversationMemorySource.stage_results.op("||")(
                            {"retain": "failed"}
                        ),
                    )
                )
                rows = (
                    await session.execute(
                        select(ConversationMemorySource, Document)
                        .join(
                            Document,
                            Document.id == ConversationMemorySource.document_id,
                        )
                        .where(
                            True if self._all_banks else self._source_scope(),
                            ConversationMemorySource.attempts < max_attempts,
                            or_(
                                (
                                    (ConversationMemorySource.status == "pending")
                                    & (ConversationMemorySource.available_at <= now)
                                ),
                                (
                                    (ConversationMemorySource.status == "processing")
                                    & or_(
                                        ConversationMemorySource.lease_expires_at
                                        <= now,
                                        ConversationMemorySource.lease_expires_at.is_(
                                            None
                                        )
                                        & (
                                            ConversationMemorySource.locked_at
                                            < expired_before
                                        ),
                                    )
                                ),
                            ),
                        )
                        .order_by(
                            ConversationMemorySource.available_at,
                            ConversationMemorySource.document_id,
                        )
                        .with_for_update(skip_locked=True, of=ConversationMemorySource)
                        .limit(limit)
                    )
                ).all()
                jobs = []
                for source, document in rows:
                    source.status = "processing"
                    source.lease_token = uuid.uuid4()
                    source.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    source.stage_results = {
                        **(getattr(source, "stage_results", None) or {}),
                        "retain": "processing",
                    }
                    source.locked_at = now
                    source.attempts += 1
                    jobs.append(self._job_from_row(source, document))
                await session.flush()
        return jobs

    async def complete(
        self, document_id: str, *, lease_token: str | None = None
    ) -> bool:
        result = await self._set_terminal_state(
            document_id, "completed", lease_token=lease_token
        )
        return result

    async def record_stages(
        self, document_id: str, stages: dict, *, lease_token: str
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.document_id == uuid.UUID(document_id),
                    self._source_scope(),
                    ConversationMemorySource.status == "processing",
                    ConversationMemorySource.lease_token == uuid.UUID(lease_token),
                    ConversationMemorySource.lease_expires_at > func.now(),
                )
                .values(
                    stage_results=ConversationMemorySource.stage_results.op("||")(
                        stages
                    )
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def retry_stage(self, document_id: str, *, stage: str = "extract") -> bool:
        if stage != "extract":
            raise ValueError("unsupported retention stage")
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.document_id == uuid.UUID(document_id),
                    self._source_scope(),
                    ConversationMemorySource.status.in_(("failed", "pending")),
                )
                .values(
                    status="pending",
                    attempts=0,
                    error_msg=None,
                    available_at=datetime.now(timezone.utc),
                    lease_token=None,
                    lease_expires_at=None,
                    locked_at=None,
                    stage_results=ConversationMemorySource.stage_results.op("||")(
                        {"retry_from": stage, "retain": "pending"}
                    ),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def fail(
        self,
        document_id: str,
        error_msg: str,
        *,
        lease_token: str | None = None,
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
                    .where(
                        ConversationMemorySource.document_id == uid,
                        self._source_scope(),
                    )
                    .with_for_update()
                )
                if source is None:
                    raise ValueError(
                        f"conversation memory source does not exist: {document_id}"
                    )
                if source.status != "processing" or str(
                    getattr(source, "lease_token", None)
                ) != str(lease_token):
                    return "cancelled"
                if (
                    getattr(source, "lease_expires_at", None) is not None
                    and source.lease_expires_at <= now
                ):
                    return "cancelled"
                if source.status == "cancelled":
                    return "cancelled"
                source.error_msg = error_msg[:2000]
                source.locked_at = None
                if source.attempts >= max_attempts:
                    source.status = "failed"
                else:
                    source.status = "pending"
                    source.available_at = now + timedelta(seconds=retry_delay_seconds)
                source.lease_token = None
                source.lease_expires_at = None
                source.stage_results = {
                    **(getattr(source, "stage_results", None) or {}),
                    "retain": source.status,
                }
                return source.status

    async def cancel_session(self, session_id: str) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.session_id == session_id,
                    self._source_scope(),
                    ConversationMemorySource.status.in_(("pending", "processing")),
                )
                .values(
                    status="cancelled",
                    locked_at=None,
                    lease_token=None,
                    lease_expires_at=None,
                    stage_results=ConversationMemorySource.stage_results.op("||")(
                        {"retain": "cancelled"}
                    ),
                    updated_at=func.now(),
                )
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def session_document_ids(self, session_id: str) -> list[str]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ConversationMemorySource.document_id).where(
                    ConversationMemorySource.session_id == session_id,
                    self._source_scope(),
                )
            )
        return [str(document_id) for document_id in rows]

    async def delete_documents(self, document_ids: list[str]) -> int:
        if not document_ids:
            return 0
        ids = [uuid.UUID(document_id) for document_id in document_ids]
        async with self._session_factory() as session:
            result = await session.execute(
                delete(Document).where(
                    Document.id.in_(ids),
                    self._document_scope(),
                    Document.file_type == CONVERSATION_FILE_TYPE,
                )
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
                    )
                    .where(self._source_scope())
                    .group_by(ConversationMemorySource.status)
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
                    ConversationMemorySource.document_id == uid,
                    self._source_scope(),
                )
            )

    async def _set_terminal_state(
        self, document_id: str, status: str, *, lease_token: str | None = None
    ) -> bool:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            result = await session.execute(
                update(ConversationMemorySource)
                .where(
                    ConversationMemorySource.document_id == uid,
                    self._source_scope(),
                    ConversationMemorySource.status == "processing",
                    ConversationMemorySource.lease_token
                    == (uuid.UUID(lease_token) if lease_token else None),
                    True
                    if lease_token is None
                    else ConversationMemorySource.lease_expires_at > func.now(),
                )
                .values(
                    status=status,
                    lease_token=None,
                    lease_expires_at=None,
                    stage_results=ConversationMemorySource.stage_results.op("||")(
                        {"retain": status}
                    ),
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
            bank_id=getattr(document, "bank_id", None) or DEFAULT_BANK_ID,
            tags=tuple(getattr(document, "tags", None) or []),
            operation_id=str(
                getattr(source, "operation_id", None) or source.document_id
            ),
            lease_token=str(source.lease_token)
            if getattr(source, "lease_token", None)
            else None,
            lease_expires_at=getattr(source, "lease_expires_at", None),
            source_context=dict(getattr(source, "source_context", None) or {}),
        )
