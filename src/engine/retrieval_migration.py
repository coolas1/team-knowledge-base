"""Durable orchestration state for scope-bounded retrieval migrations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from src.engine.components.store.models import Document
from src.engine.components.store.scope import scope_predicate
from src.engine.hindsight_components.models import (
    RetrievalMigrationDocument,
    RetrievalMigrationRun,
)
from src.engine.scope import MemoryScope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MigrationLease:
    run_id: str
    token: str
    generation: str
    expires_at: datetime


class RetrievalMigrationStore:
    """Own migration leases, retry state, fences, checkpoints, and budgets."""

    def __init__(self, sessions, *, scope: MemoryScope):
        self.sessions = sessions
        self.scope = scope

    async def prepare(
        self,
        document_ids: list[str],
        *,
        token_limit: int = 1_000_000,
        cost_limit_usd: float = 0.0,
        max_attempts: int = 10,
    ) -> str:
        if token_limit < 1 or cost_limit_usd < 0 or max_attempts < 1:
            raise ValueError("migration limits must be positive")
        identities = [uuid.UUID(value) for value in dict.fromkeys(document_ids)]
        async with self.sessions() as session, session.begin():
            documents = list(
                await session.scalars(
                    select(Document)
                    .where(
                        Document.id.in_(identities),
                        Document.is_current.is_(True),
                        scope_predicate(Document.bank_id, Document.tags, self.scope),
                    )
                    .order_by(Document.id)
                )
            )
            if len(documents) != len(identities):
                raise ValueError("migration documents are missing or outside scope")
            run = RetrievalMigrationRun(
                bank_id=self.scope.bank_id,
                token_limit=token_limit,
                cost_limit_usd=cost_limit_usd,
                max_attempts=max_attempts,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                RetrievalMigrationDocument(
                    run_id=run.id,
                    document_id=document.id,
                    expected_revision=document.version_number,
                    expected_generation=document.processing_generation,
                )
                for document in documents
            )
            return str(run.id)

    async def claim(
        self,
        run_id: str,
        *,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> MigrationLease | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or _utcnow()
        async with self.sessions() as session, session.begin():
            run = await self._run(session, run_id, lock=True)
            if run.stage in {"verified", "failed"}:
                return None
            if run.lease_expires_at is not None and run.lease_expires_at > claimed_at:
                return None
            if run.attempts >= run.max_attempts:
                run.stage = "failed"
                run.progress = {**run.progress, "last_error": "attempt_limit"}
                return None
            # A process interruption leaves its document in processing. The
            # expired scope lease is the authority to resume that checkpoint.
            await session.execute(
                update(RetrievalMigrationDocument)
                .where(
                    RetrievalMigrationDocument.run_id == run.id,
                    RetrievalMigrationDocument.status == "processing",
                )
                .values(status="pending", error_code="interrupted")
            )
            token = uuid.uuid4()
            expires_at = claimed_at + timedelta(seconds=lease_seconds)
            run.lease_token = token
            run.lease_expires_at = expires_at
            run.attempts += 1
            return MigrationLease(
                run_id=str(run.id),
                token=str(token),
                generation=str(run.generation),
                expires_at=expires_at,
            )

    async def next_document(self, lease: MigrationLease):
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            document = await session.scalar(
                select(RetrievalMigrationDocument)
                .where(
                    RetrievalMigrationDocument.run_id == run.id,
                    RetrievalMigrationDocument.status.in_(("pending", "failed")),
                    RetrievalMigrationDocument.attempts < run.max_attempts,
                )
                .order_by(RetrievalMigrationDocument.document_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if document is None:
                return None
            document.status = "processing"
            document.attempts += 1
            document.error_code = None
            return document

    async def checkpoint(
        self,
        lease: MigrationLease,
        document_id: str,
        status: str,
        **details,
    ) -> bool:
        if status not in {"backfilled", "validated", "verified"}:
            raise ValueError("invalid document checkpoint")
        identity = uuid.UUID(document_id)
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            target = await session.get(
                RetrievalMigrationDocument, (run.id, identity), with_for_update=True
            )
            current = await session.get(Document, identity)
            if target is None or target.status != "processing":
                raise ValueError("migration document is not claimed")
            if (
                current is None
                or not current.is_current
                or current.version_number != target.expected_revision
                or current.processing_generation != target.expected_generation
            ):
                target.status = "skipped_changed"
                target.error_code = "revision_or_generation_changed"
                return False
            target.status = status
            target.checkpoint = {**target.checkpoint, **details}
            return True

    async def fail_document(
        self, lease: MigrationLease, document_id: str, error_code: str
    ) -> None:
        if not error_code or len(error_code) > 80:
            raise ValueError("error_code must be a short sanitized identifier")
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            target = await session.get(
                RetrievalMigrationDocument,
                (run.id, uuid.UUID(document_id)),
                with_for_update=True,
            )
            if target is None or target.status != "processing":
                raise ValueError("migration document is not claimed")
            target.status = "failed"
            target.error_code = error_code

    async def charge(
        self, lease: MigrationLease, *, tokens: int, cost_usd: float = 0.0
    ) -> bool:
        if tokens < 0 or cost_usd < 0:
            raise ValueError("usage cannot be negative")
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            if run.tokens_used + tokens > run.token_limit or (
                run.cost_limit_usd and run.cost_used_usd + cost_usd > run.cost_limit_usd
            ):
                run.progress = {**run.progress, "budget_exhausted": True}
                return False
            run.tokens_used += tokens
            run.cost_used_usd += cost_usd
            return True

    async def release(self, lease: MigrationLease) -> None:
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            run.lease_token = None
            run.lease_expires_at = None

    async def _run(self, session, run_id: str, *, lock: bool = False):
        statement = select(RetrievalMigrationRun).where(
            RetrievalMigrationRun.id == uuid.UUID(run_id),
            RetrievalMigrationRun.bank_id == self.scope.bank_id,
        )
        run = await session.scalar(statement.with_for_update() if lock else statement)
        if run is None:
            raise ValueError("migration run is missing or outside scope")
        return run

    async def _leased_run(self, session, lease: MigrationLease, *, lock: bool):
        run = await self._run(session, lease.run_id, lock=lock)
        if run.lease_token != uuid.UUID(lease.token):
            raise ValueError("migration lease is no longer owned")
        if run.generation != uuid.UUID(lease.generation):
            raise ValueError("migration generation changed")
        return run
