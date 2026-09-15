from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    MemoryBank,
)
from src.engine.components.store.postgres import async_session_factory, engine, init_db
from src.engine.hindsight_components.models import (
    MemoryUnit,
    RetrievalMigrationDocument,
    RetrievalMigrationRun,
)
from src.engine.retrieval_migration import (
    RetrievalBackfillWorker,
    RetrievalMigrationStore,
)
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
            await session.execute(
                delete(RetrievalMigrationRun).where(
                    RetrievalMigrationRun.bank_id == bank_id
                )
            )
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
        await engine.dispose()


async def test_dry_run_manifest_reports_work_and_performs_no_writes() -> None:
    await init_db()
    bank_id = f"migration-preview-{uuid.uuid4().hex[:8]}"
    ready_id, missing_id, conversation_id = (uuid.uuid4() for _ in range(3))
    target_model = "fixture-embedding-v2"
    try:
        async with async_session_factory() as session, session.begin():
            session.add(MemoryBank(id=bank_id, name=bank_id))
            await session.flush()
            session.add_all(
                [
                    Document(
                        id=ready_id,
                        bank_id=bank_id,
                        title="ready",
                        file_type="markdown",
                        raw_text="ready body",
                        status="indexed",
                    ),
                    Document(
                        id=missing_id,
                        bank_id=bank_id,
                        title="missing",
                        file_type="pdf",
                        raw_text="missing body",
                        status="indexed",
                    ),
                    Document(
                        id=conversation_id,
                        bank_id=bank_id,
                        title="transcript",
                        file_type="conversation",
                        raw_text="protected transcript",
                        status="indexed",
                    ),
                ]
            )
            await session.flush()
            session.add(
                DocumentRetrieval(
                    doc_id=ready_id,
                    bank_id=bank_id,
                    revision=1,
                    title="ready",
                    embedding=[0.1] * 768,
                    embedding_model=target_model,
                    generation_state="ready",
                )
            )
            session.add_all(
                [
                    Chunk(
                        doc_id=ready_id,
                        bank_id=bank_id,
                        chunk_index=0,
                        chunk_text="ready body",
                        doc_uri="ready:0",
                        token_count=2,
                        embedding=[0.1] * 768,
                    ),
                    Chunk(
                        doc_id=missing_id,
                        bank_id=bank_id,
                        chunk_index=0,
                        chunk_text="missing body",
                        doc_uri="missing:0",
                        token_count=3,
                    ),
                ]
            )
            session.add_all(
                [
                    MemoryUnit(
                        id=uuid.uuid4(),
                        bank_id=bank_id,
                        document_id=ready_id,
                        chunk_index=0,
                        memory_index=0,
                        text="ready body",
                        source_text="ready body",
                        lexical_tokens=["ready"],
                    ),
                    MemoryUnit(
                        id=uuid.uuid4(),
                        bank_id=bank_id,
                        document_id=missing_id,
                        chunk_index=0,
                        memory_index=0,
                        text="missing body",
                        source_text="missing body",
                        lexical_tokens=None,
                    ),
                ]
            )

        store = RetrievalMigrationStore(
            async_session_factory, scope=MemoryScope(bank_id=bank_id)
        )
        async with async_session_factory() as session:
            before = {
                "runs": await session.scalar(
                    select(func.count()).select_from(RetrievalMigrationRun)
                ),
                "parents": await session.scalar(
                    select(func.count()).select_from(DocumentRetrieval)
                ),
                "chunks": await session.scalar(select(func.count()).select_from(Chunk)),
                "memories": await session.scalar(
                    select(func.count()).select_from(MemoryUnit)
                ),
            }
        manifest = await store.dry_run_manifest(target_embedding_model=target_model)
        repeated = await store.dry_run_manifest(target_embedding_model=target_model)
        assert manifest == repeated
        assert manifest["summary"] == {
            "documents": 2,
            "missing_or_stale_parents": 1,
            "chunks": 2,
            "chunk_vectors": 1,
            "lexical_eligible": 2,
            "lexical_missing": 1,
            "estimated_parent_embeddings": 1,
            "estimated_chunk_embeddings": 2,
            "estimated_tokens": 5,
        }
        assert manifest["protected"]["conversation_documents"] == 1
        assert manifest["documents"][0]["chunks"]["model_state"] == {"untracked": 1}
        assert len(manifest["checksum"]) == 64
        async with async_session_factory() as session:
            after = {
                "runs": await session.scalar(
                    select(func.count()).select_from(RetrievalMigrationRun)
                ),
                "parents": await session.scalar(
                    select(func.count()).select_from(DocumentRetrieval)
                ),
                "chunks": await session.scalar(select(func.count()).select_from(Chunk)),
                "memories": await session.scalar(
                    select(func.count()).select_from(MemoryUnit)
                ),
            }
        assert after == before
    finally:
        async with async_session_factory() as session, session.begin():
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
        await engine.dispose()


async def test_backfill_resumes_without_duplicates_and_fences_concurrent_edit() -> None:
    await init_db()
    bank_id = f"migration-worker-{uuid.uuid4().hex[:8]}"
    first_id, changed_id = sorted((uuid.uuid4(), uuid.uuid4()))
    generations = {first_id: uuid.uuid4(), changed_id: uuid.uuid4()}
    now = datetime.now(timezone.utc)
    try:
        async with async_session_factory() as session, session.begin():
            session.add(MemoryBank(id=bank_id, name=bank_id))
            await session.flush()
            for document_id in (first_id, changed_id):
                session.add(
                    Document(
                        id=document_id,
                        bank_id=bank_id,
                        title=str(document_id),
                        file_type="markdown",
                        raw_text="original source",
                        overview="clean overview",
                        status="indexed",
                        processing_generation=generations[document_id],
                    )
                )
            await session.flush()
            for document_id in (first_id, changed_id):
                session.add(
                    Chunk(
                        doc_id=document_id,
                        bank_id=bank_id,
                        chunk_index=0,
                        chunk_text=f"original chunk {document_id}",
                        doc_uri=f"{document_id}:0",
                    )
                )

        store = RetrievalMigrationStore(
            async_session_factory, scope=MemoryScope(bank_id=bank_id)
        )
        run_id = await store.prepare(
            [str(first_id), str(changed_id)], token_limit=10_000
        )
        first_lease = await store.claim(run_id, lease_seconds=1, now=now)
        assert first_lease is not None
        interrupted_target = await store.next_document(first_lease)
        assert interrupted_target is not None

        calls = []

        async def embed(values):
            calls.extend(values)
            return [[float(len(value) % 7)] * 768 for value in values]

        resumed = await store.claim(
            run_id, lease_seconds=60, now=now + timedelta(seconds=2)
        )
        assert resumed is not None
        worker = RetrievalBackfillWorker(
            store, embed, embedding_model="fixture-v2", batch_size=1
        )
        first = await worker.process_next(resumed)
        assert first.status == "backfilled"
        assert any(value.startswith("original chunk") for value in calls)
        async with async_session_factory() as session:
            assert (
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(DocumentRetrieval)
                        .where(DocumentRetrieval.doc_id == uuid.UUID(first.document_id))
                    )
                    or 0
                )
                == 1
            )

        async def edit_during_embedding(values):
            if any(value.startswith(str(changed_id)) for value in values):
                async with async_session_factory() as session, session.begin():
                    changed = await session.get(Document, changed_id)
                    changed.version_number += 1
                    changed.processing_generation = uuid.uuid4()
            return [[0.2] * 768 for _ in values]

        changed_worker = RetrievalBackfillWorker(
            store, edit_during_embedding, embedding_model="fixture-v2", batch_size=1
        )
        changed = await changed_worker.process_next(resumed)
        assert changed.status == "skipped_changed"
        assert (await changed_worker.process_next(resumed)).status == "complete"
        async with async_session_factory() as session:
            assert await session.get(DocumentRetrieval, changed_id) is None
            chunk = await session.scalar(select(Chunk).where(Chunk.doc_id == first_id))
            assert chunk.embedding_model == "fixture-v2"
            assert chunk.chunk_text.startswith("original chunk")
            assert (
                int(
                    await session.scalar(
                        select(func.count())
                        .select_from(DocumentRetrieval)
                        .where(DocumentRetrieval.doc_id == first_id)
                    )
                    or 0
                )
                == 1
            )
    finally:
        async with async_session_factory() as session, session.begin():
            await session.execute(
                delete(RetrievalMigrationRun).where(
                    RetrievalMigrationRun.bank_id == bank_id
                )
            )
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
        await engine.dispose()
