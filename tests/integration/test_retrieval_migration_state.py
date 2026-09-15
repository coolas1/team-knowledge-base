from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from src.engine.components.store.models import Document, MemoryBank
from src.engine.components.store.postgres import async_session_factory, init_db
from src.engine.hindsight_components.models import RetrievalMigrationDocument
from src.engine.retrieval_migration import RetrievalMigrationStore
from src.engine.scope import MemoryScope

pytestmark = pytest.mark.integration


async def test_migration_lease_resume_retry_budget_and_revision_fence() -> None:
    await init_db()
    bank_id = f"migration-state-{uuid.uuid4().hex[:8]}"
    document_ids = [uuid.uuid4(), uuid.uuid4()]
    generation = uuid.uuid4()
    now = datetime.now(timezone.utc)
    try:
        async with async_session_factory() as session, session.begin():
            session.add(MemoryBank(id=bank_id, name=bank_id))
            await session.flush()
            session.add_all(
                Document(
                    id=document_id,
                    bank_id=bank_id,
                    title=f"document-{index}",
                    file_type="markdown",
                    raw_text="migration fixture",
                    status="indexed",
                    processing_generation=generation,
                )
                for index, document_id in enumerate(document_ids)
            )
        store = RetrievalMigrationStore(
            async_session_factory, scope=MemoryScope(bank_id=bank_id)
        )
        run_id = await store.prepare(
            [str(value) for value in document_ids],
            token_limit=100,
            cost_limit_usd=1.0,
            max_attempts=4,
        )
        first = await store.claim(run_id, lease_seconds=10, now=now)
        assert first is not None
        assert await store.claim(run_id, lease_seconds=10, now=now) is None

        interrupted = await store.next_document(first)
        assert interrupted is not None
        resumed = await store.claim(
            run_id, lease_seconds=10, now=now + timedelta(seconds=11)
        )
        assert resumed is not None and resumed.token != first.token
        retried = await store.next_document(resumed)
        assert retried.document_id == interrupted.document_id
        assert retried.attempts == 2
        await store.fail_document(resumed, str(retried.document_id), "provider_timeout")
        retried = await store.next_document(resumed)
        assert retried.document_id == interrupted.document_id
        assert await store.checkpoint(
            resumed, str(retried.document_id), "backfilled", rows=3
        )

        second = await store.next_document(resumed)
        assert second is not None
        async with async_session_factory() as session, session.begin():
            changed = await session.get(Document, second.document_id)
            changed.version_number += 1
            changed.processing_generation = uuid.uuid4()
        assert not await store.checkpoint(
            resumed, str(second.document_id), "backfilled", rows=1
        )
        assert await store.charge(resumed, tokens=80, cost_usd=0.5)
        assert not await store.charge(resumed, tokens=21, cost_usd=0.1)
        await store.release(resumed)

        async with async_session_factory() as session:
            rows = list(
                await session.scalars(
                    select(RetrievalMigrationDocument)
                    .where(RetrievalMigrationDocument.run_id == uuid.UUID(run_id))
                    .order_by(RetrievalMigrationDocument.document_id)
                )
            )
        assert {row.status for row in rows} == {"backfilled", "skipped_changed"}
        skipped = next(row for row in rows if row.status == "skipped_changed")
        assert skipped.error_code == "revision_or_generation_changed"
    finally:
        async with async_session_factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
