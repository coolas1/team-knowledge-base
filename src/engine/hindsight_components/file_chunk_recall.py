"""Recall original file vectors without retaining their text as memory facts."""

import uuid
from sqlalchemy import case, func, select

from src.engine.components.store.models import Chunk, Document, public_document_filter
from src.engine.components.store.scope import scope_predicate, tag_predicate
from .types import RecallCandidate, RecallFilter
from .utils import lexical_tokens


def _conditions(scope, filters):
    conditions = [
        public_document_filter(scope),
        scope_predicate(Chunk.bank_id, Chunk.tags, scope),
        Document.status == "indexed",
        Document.is_current.is_(True),
    ]
    if filters.tags is not None:
        conditions.append(tag_predicate(Chunk.tags, filters.tags))
    if filters.reference_time is not None:
        conditions.append(Document.updated_at <= filters.reference_time)
    return conditions


def _candidate(chunk, document, score=0):
    return RecallCandidate(
        id=str(chunk.id),
        document_id=str(document.id),
        title=document.title,
        text=chunk.chunk_text,
        source_text=chunk.chunk_text,
        chunk_index=chunk.chunk_index,
        source_type="upload",
        context="Original file passage",
        mentioned_at=document.updated_at.isoformat(),
        updated_at=document.updated_at.isoformat(),
        metadata={"is_source_chunk": True, "source_kind": "file_chunk"},
        embedding=list(chunk.embedding) if chunk.embedding is not None else None,
        semantic_score=float(score),
    )


def _excluded(source_type, filters):
    return (
        "chunks" not in filters.include
        or (filters.memory_types and "world" not in filters.memory_types)
        or (source_type is not None and source_type != "upload")
        or (filters.source_types and "upload" not in filters.source_types)
    )


async def search_file_chunks(sessions, scope, embedding, limit, source_type, filters):
    filters = filters or RecallFilter()
    if _excluded(source_type, filters):
        return []
    score = (1 - Chunk.embedding.cosine_distance(embedding)).label("score")
    async with sessions() as session:
        rows = await session.execute(
            select(Chunk, Document, score)
            .join(Document, Document.id == Chunk.doc_id)
            .where(*_conditions(scope, filters), Chunk.embedding.is_not(None))
            .order_by(score.desc())
            .limit(limit)
        )
        return [_candidate(c, d, value) for c, d, value in rows]


async def search_file_keywords(
    sessions, scope, query, limit, source_type, filters, *, candidate_limit, scorer
):
    filters = filters or RecallFilter()
    tokens = list(dict.fromkeys(lexical_tokens(query)))[:64]
    if _excluded(source_type, filters) or not tokens:
        return []
    # Bounded keyword candidates complement vectors for exact names/numbers.
    overlap = sum(
        case(
            (func.lower(Chunk.chunk_text).contains(token, autoescape=True), 1), else_=0
        )
        for token in tokens
    )
    async with sessions() as session:
        rows = list(
            await session.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.doc_id)
                .where(*_conditions(scope, filters), overlap > 0)
                .order_by(overlap.desc(), Chunk.id)
                .limit(candidate_limit)
            )
        )
    scores = scorer(query, [c.chunk_text for c, _ in rows])
    candidates = []
    for (chunk, document), score in zip(rows, scores, strict=True):
        if score > 0:
            candidate = _candidate(chunk, document)
            candidate.semantic_score = None
            candidate.keyword_score = score
            candidates.append(candidate)
    return sorted(candidates, key=lambda c: c.keyword_score, reverse=True)[:limit]


async def expand_file_chunk(sessions, scope, identity):
    async with sessions() as session:
        row = (
            await session.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.doc_id)
                .where(
                    Chunk.id == uuid.UUID(str(identity)),
                    *_conditions(scope, RecallFilter()),
                )
            )
        ).one_or_none()
        if row is None:
            return None
        chunk, document = row
        return {
            "memory": _candidate(chunk, document).as_evidence(),
            "chunk": {
                "id": str(chunk.id),
                "document_id": str(document.id),
                "chunk_index": chunk.chunk_index,
                "text": chunk.chunk_text,
            },
            "document": {
                "id": str(document.id),
                "title": document.title,
                "file_type": document.file_type,
                "text": document.raw_text,
                "updated_at": document.updated_at.isoformat(),
            },
            "source_facts": [],
            "freshness": "active",
            "updated_at": document.updated_at.isoformat(),
        }
