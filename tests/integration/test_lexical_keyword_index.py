from __future__ import annotations

import asyncio
import math
import time
import uuid

import pytest
from sqlalchemy import delete, func, insert, select, update

from src.engine.components.store.models import Document
from src.engine.components.store.postgres import async_session_factory, init_db
from config.settings import settings
from src.engine.hindsight_components.lexical_backfill import run_lexical_backfill
from src.engine.hindsight_components.models import MemoryUnit
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.utils import lexical_tokens

pytestmark = pytest.mark.integration


async def test_lexical_backfill_is_resumable_and_indexed_search_is_bounded(
    monkeypatch,
) -> None:
    await init_db()
    document_id = uuid.uuid4()
    token = f"scale{uuid.uuid4().hex}"
    count = 30_000
    try:
        async with async_session_factory() as session:
            session.add(
                Document(
                    id=document_id,
                    title="lexical-scale-test.md",
                    file_type="markdown",
                    raw_text="integration fixture",
                    overview="",
                    status="indexed",
                )
            )
            await session.flush()
            for offset in range(0, count, 1_000):
                rows = [
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "chunk_index": index,
                        "memory_index": 0,
                        "memory_type": "world",
                        "text": f"{token} 知识库 item {index}",
                        "source_text": f"source {index}",
                        "context": "scale test",
                        "embedding": None,
                        "confidence": 1.0,
                        "is_source_chunk": False,
                        "proof_count": 1,
                        "source_memory_ids": [],
                        "tags": [],
                        "state": "active",
                        "metadata_json": {},
                        "lexical_tokens": None,
                    }
                    for index in range(offset, min(count, offset + 1_000))
                ]
                await session.execute(insert(MemoryUnit), rows)
            await session.commit()

        monkeypatch.setattr(settings, "hindsight_keyword_index_enabled", True)
        with pytest.raises(RuntimeError, match="complete lexical backfill"):
            await init_db()

        # Simulate a committed batch before a worker restart.
        async with async_session_factory() as session:
            first_ids = list(
                await session.scalars(
                    select(MemoryUnit.id)
                    .where(MemoryUnit.document_id == document_id)
                    .order_by(MemoryUnit.id)
                    .limit(100)
                )
            )
            await session.execute(
                update(MemoryUnit)
                .where(MemoryUnit.id.in_(first_ids))
                .values(lexical_tokens=lexical_tokens(f"{token} 知识库"))
            )
            await session.commit()

        async def insert_during_backfill() -> None:
            await asyncio.sleep(0.01)
            async with async_session_factory() as session:
                await session.execute(
                    insert(MemoryUnit),
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "chunk_index": count,
                        "memory_index": 0,
                        "memory_type": "world",
                        "text": f"{token} concurrent memory",
                        "source_text": "concurrent source",
                        "context": "scale test",
                        "embedding": None,
                        "confidence": 1.0,
                        "is_source_chunk": False,
                        "proof_count": 1,
                        "source_memory_ids": [],
                        "tags": [],
                        "state": "active",
                        "metadata_json": {},
                        "lexical_tokens": lexical_tokens(f"{token} concurrent memory"),
                    },
                )
                await session.commit()

        report, _ = await asyncio.gather(
            run_lexical_backfill(
                async_session_factory,
                batch_size=1_000,
                document_id=str(document_id),
            ),
            insert_during_backfill(),
        )
        assert report.updated == count - 100
        assert report.complete is True
        await init_db()
        repeated = await run_lexical_backfill(
            async_session_factory,
            batch_size=1_000,
            document_id=str(document_id),
        )
        assert repeated.updated == 0

        repository = PostgresMemoryRepository(
            keyword_index_enabled=True,
            keyword_candidate_limit=300,
        )
        latencies = []
        results = []
        for _ in range(10):
            started = time.perf_counter()
            results = await repository.keyword_search(token, 50)
            latencies.append(time.perf_counter() - started)
        p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
        print(f"lexical_30k_p95_ms={p95 * 1000:.2f}")
        assert len(results) == 50
        assert {item.document_id for item in results} == {str(document_id)}
        assert p95 < 2.0

        async with async_session_factory() as session:
            stored = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryUnit)
                    .where(MemoryUnit.document_id == document_id)
                )
                or 0
            )
        assert stored == count + 1
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()
