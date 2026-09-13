"""Retrieval-view backfill: OCR-noisy documents become findable by title.

Mirrors the 2026-09-12 incident: a scanned paper whose extracted body is
OCR noise never competed on embeddings (an unrelated recipe actually
outscored it), and its lexical tokens carried no title terms. After the
backfill, the clean title/filename/overview prefix makes it outrank the
distractor and match the keyword arm, while every displayed text stays the
original extraction.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from src.engine.components.embedder import embedder
from src.engine.components.store.models import Chunk, Document, MemoryBank
from src.engine.components.store.postgres import async_session_factory, engine, init_db
from src.engine.hindsight_components.file_chunk_recall import search_file_chunks
from src.engine.hindsight_components.models import MemoryUnit
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.types import RecallFilter
from src.engine.hindsight_components.utils import lexical_tokens
from src.engine.retrieval_view_backfill import backfill_retrieval_views
from src.engine.scope import MemoryScope

pytestmark = pytest.mark.integration

QUERY = "有哪些和自动驾驶相关的论文"

CLEAN_BODY = "本文讨论自动驾驶的控制理论与轨迹规划方法。"
NOISY_BODY = "锟斤拷烫烫烫 屯屯屯 屯屯屯 隔壁老王Recording"
FERMENT_BODY = "家庭酿造食谱：米曲霉接种、温度控制与发酵配方记录。"

DOCS = (
    # (key, title, body, overview)
    ("clean", "自动驾驶论文一", CLEAN_BODY, "自动驾驶控制理论综述"),
    ("noisy", "自动驾驶论文二", NOISY_BODY, "关于自动驾驶控制理论的综述论文"),
    ("ferment", "家庭酿造入门", FERMENT_BODY, "米曲霉接种与发酵温度控制指南"),
)


async def _seed_documents(bank_id: str) -> dict[str, uuid.UUID]:
    """Pre-backfill state: vectors and tokens from the raw text only."""
    doc_ids = {key: uuid.uuid4() for key, *_ in DOCS}
    for (key, title, body, overview), doc_id in zip(DOCS, doc_ids.values()):
        embedding = await embedder.embed_text(body)
        async with async_session_factory() as session:
            session.add(
                Document(
                    id=doc_id,
                    bank_id=bank_id,
                    title=title,
                    file_type="pdf",
                    raw_text=body,
                    overview=overview,
                    file_path=f"/uploads/{bank_id}/{doc_id}.pdf",
                    status="indexed",
                )
            )
            session.add(
                Chunk(
                    doc_id=doc_id,
                    bank_id=bank_id,
                    chunk_index=0,
                    chunk_text=body,
                    overview=overview,
                    doc_uri=f"{doc_id}:{title}",
                    token_count=8,
                    embedding=embedding,
                )
            )
            session.add(
                MemoryUnit(
                    id=uuid.uuid4(),
                    bank_id=bank_id,
                    document_id=doc_id,
                    chunk_index=0,
                    memory_index=0,
                    memory_type="world",
                    text=body,
                    source_text=body,
                    context="seed",
                    embedding=embedding,
                    confidence=1.0,
                    is_source_chunk=True,
                    proof_count=1,
                    source_memory_ids=[],
                    tags=[],
                    scope_tags=[],
                    state="active",
                    memory_version=1,
                    metadata_json={"title": title},
                    lexical_tokens=lexical_tokens(body),
                )
            )
            await session.commit()
    return doc_ids


async def _semantic_scores(
    scope: MemoryScope, query: str
) -> dict[str, float]:
    """Per-document cosine from the file-chunk semantic arm — the score the
    recall gate's semantic floor is applied to."""
    embedding = await embedder.embed_text(query)
    candidates = await search_file_chunks(
        async_session_factory, scope, embedding, 10, None, RecallFilter()
    )
    return {item.document_id: float(item.semantic_score or 0.0) for item in candidates}


async def test_backfill_surfaces_noisy_documents_by_title() -> None:
    await init_db()
    bank_id = f"backfill-test-{uuid.uuid4().hex[:8]}"
    scope = MemoryScope(bank_id=bank_id)
    try:
        async with async_session_factory() as session:
            session.add(MemoryBank(id=bank_id, name=bank_id))
            await session.commit()
        doc_ids = await _seed_documents(bank_id)

        # The incident: the noisy paper is not findable — the keyword arm has
        # no title tokens to match, and its embedding loses to the recipe.
        repository = PostgresMemoryRepository(
            keyword_index_enabled=True, scope=scope
        )
        keyword_before = await repository.keyword_search("自动驾驶 论文", 10)
        assert str(doc_ids["noisy"]) not in {item.document_id for item in keyword_before}
        before = await _semantic_scores(scope, QUERY)
        assert before[str(doc_ids["noisy"])] < before[str(doc_ids["ferment"])]

        stats = await backfill_retrieval_views(
            async_session_factory, embedder.embed_batch, bank_id=bank_id
        )
        assert stats == {"documents": 3, "chunks": 3, "memories": 3}

        # Keyword arm matches the title through the rebuilt lexical tokens.
        keyword_after = await repository.keyword_search("自动驾驶 论文", 10)
        assert str(doc_ids["noisy"]) in {item.document_id for item in keyword_after}

        # Semantic arm: the paper now outranks the recipe and clears the
        # 0.45 floor the recall gate applies.
        after = await _semantic_scores(scope, QUERY)
        noisy_id, ferment_id = str(doc_ids["noisy"]), str(doc_ids["ferment"])
        assert after[noisy_id] > after[ferment_id]
        assert after[noisy_id] >= 0.45
        assert after[noisy_id] - before[noisy_id] >= 0.05

        # Displayed/evidence text stays the original extraction.
        async with async_session_factory() as session:
            units = list(
                await session.scalars(
                    select(MemoryUnit).where(
                        MemoryUnit.document_id == doc_ids["noisy"],
                        MemoryUnit.state == "active",
                    )
                )
            )
            chunk_texts = list(
                await session.scalars(
                    select(Chunk.chunk_text).where(Chunk.doc_id == doc_ids["noisy"])
                )
            )
        assert units and units[0].lexical_tokens is not None
        assert "自动" in units[0].lexical_tokens  # title bigram, absent from the body
        assert units[0].text == NOISY_BODY
        assert chunk_texts == [NOISY_BODY]
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(delete(MemoryBank).where(MemoryBank.id == bank_id))
            await session.commit()
        await engine.dispose()
