"""Transactional dual-write and publication fences for document retrieval."""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, select, update

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    MemoryBank,
)
from src.engine.components.store.postgres import async_session_factory, engine, init_db
from src.engine.graphrag import pipeline as pipeline_module
from src.engine.graphrag.pipeline import Pipeline, StaleDocumentGeneration

pytestmark = pytest.mark.integration


class _Embedder:
    _model = "dual-write-test"

    async def embed_batch(self, texts):
        return [[float(index + 1) / 1000] * 768 for index, _ in enumerate(texts)]


async def _persist(pipe: Pipeline, doc_id: uuid.UUID, fence, *, title="guide.md"):
    async with async_session_factory() as session:
        await pipe._persist_chunks(
            session,
            doc_id=doc_id,
            title=title,
            raw_text="current body",
            content_hash="hash-current",
            overview="current overview",
            filename=title,
            entities=["entity"],
            document_embedding=[0.2] * 768,
            chunks=[SimpleNamespace(index=0, text="current body", token_count=2)],
            embeddings=[[0.3] * 768],
            fence=fence,
        )


async def test_dual_write_fences_retry_failure_revision_and_metadata(monkeypatch):
    await init_db()
    bank_id = f"dual-write-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    pipe = Pipeline(SimpleNamespace(), analyzer=SimpleNamespace())
    monkeypatch.setattr(pipeline_module, "embedder", _Embedder())
    try:
        async with async_session_factory() as session:
            session.add(MemoryBank(id=bank_id, name=bank_id))
            await session.flush()
            session.add(
                Document(
                    id=doc_id,
                    bank_id=bank_id,
                    title="guide.md",
                    file_type="markdown",
                    file_path=f"/uploads/{doc_id}/guide.md",
                    status="pending",
                )
            )
            await session.commit()

        # A retry owns a new generation; the older in-flight result cannot publish.
        old_fence = await pipe._begin_processing(doc_id)
        retry_fence = await pipe._begin_processing(doc_id)
        assert old_fence is not None and retry_fence is not None
        with pytest.raises(StaleDocumentGeneration):
            await _persist(pipe, doc_id, old_fence)
        await _persist(pipe, doc_id, retry_fence)

        async with async_session_factory() as session:
            rows = list(
                await session.scalars(
                    select(DocumentRetrieval).where(DocumentRetrieval.doc_id == doc_id)
                )
            )
            assert len(rows) == 1
            assert rows[0].revision == 1
            assert rows[0].generation_state == "ready"
            assert await session.scalar(
                select(Chunk.id).where(Chunk.doc_id == doc_id)
            ) is not None

        # A stale failure callback is fenced and cannot poison the successful retry.
        await pipe._mark_failed(doc_id, RuntimeError("late"), "test", fence=old_fence)
        async with async_session_factory() as session:
            assert (await session.get(Document, doc_id)).status == "indexed"
            assert (
                await session.get(DocumentRetrieval, doc_id)
            ).generation_state == "ready"

        # Metadata-only changes rebuild the same row and its clean field tokens.
        async with async_session_factory() as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(
                    title="renamed-guide.md",
                    overview="new metadata summary",
                    tags=["release"],
                )
            )
            await session.commit()
        assert await pipe.refresh_document_retrieval(doc_id) is True
        async with async_session_factory() as session:
            parent = await session.get(DocumentRetrieval, doc_id)
            assert parent.title == "renamed-guide.md"
            assert parent.overview == "new metadata summary"
            assert parent.tags == ["release"]
            assert parent.embedding_model == "dual-write-test"
            assert {"renamed", "guide", "release"} <= set(parent.field_tokens)

        # Once the revision retires, neither a late success nor refresh can publish.
        stale_fence = await pipe._begin_processing(doc_id)
        assert stale_fence is not None
        async with async_session_factory() as session:
            await session.execute(
                update(Document).where(Document.id == doc_id).values(is_current=False)
            )
            await session.execute(
                update(DocumentRetrieval)
                .where(DocumentRetrieval.doc_id == doc_id)
                .values(generation_state="failed")
            )
            await session.commit()
        with pytest.raises(StaleDocumentGeneration):
            await _persist(pipe, doc_id, stale_fence, title="renamed-guide.md")
        assert await pipe.refresh_document_retrieval(doc_id) is False
        async with async_session_factory() as session:
            assert (
                await session.get(DocumentRetrieval, doc_id)
            ).generation_state == "failed"
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Document).where(Document.id == doc_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
            await session.commit()
        await engine.dispose()
