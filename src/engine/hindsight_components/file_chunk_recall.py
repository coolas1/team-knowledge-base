"""Recall original file vectors without retaining their text as memory facts."""

import uuid
from pathlib import PurePath
from sqlalchemy import case, func, or_, select

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    public_document_filter,
)
from src.engine.components.store.scope import scope_predicate, tag_predicate
from .types import RecallCandidate, RecallFilter
from .utils import lexical_tokens
from .retrieval_flags import bank_read_enabled


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


def fielded_lexical_scores(query, fields, scorer, weights):
    """Return total scores and per-field weighted contributions."""
    contributions = {
        name: [score * weights[name] for score in scorer(query, texts)]
        for name, texts in fields.items()
    }
    row_count = len(next(iter(fields.values()), []))
    totals = [
        sum(contributions[name][index] for name in contributions)
        for index in range(row_count)
    ]
    return totals, contributions


def reliable_passage(score: float, threshold: float) -> bool:
    """Keep passage text only when its independently measured score is reliable."""
    return float(score) >= threshold


async def search_file_chunks(
    sessions,
    scope,
    embedding,
    limit,
    source_type,
    filters,
    *,
    hierarchical_enabled=None,
):
    filters = filters or RecallFilter()
    if _excluded(source_type, filters):
        return []
    from config.settings import settings

    if hierarchical_enabled is None:
        hierarchical_enabled = await bank_read_enabled(
            sessions,
            scope,
            "hierarchical_retrieval_enabled",
            process_enabled=settings.hindsight_hierarchical_retrieval_enabled,
        )
    if hierarchical_enabled:
        return await _search_hierarchical_chunks(
            sessions, scope, embedding, limit, filters
        )
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


async def _search_hierarchical_chunks(sessions, scope, embedding, limit, filters):
    """Select revision-fenced parents, then rank original passages within them."""
    from config.settings import settings

    parent_score = (1 - DocumentRetrieval.embedding.cosine_distance(embedding)).label(
        "parent_score"
    )
    parent_limit = min(max(limit * 2, 5), settings.hindsight_keyword_candidate_limit)
    parent_conditions = [
        public_document_filter(scope),
        scope_predicate(DocumentRetrieval.bank_id, DocumentRetrieval.tags, scope),
        DocumentRetrieval.embedding.is_not(None),
        DocumentRetrieval.generation_state == "ready",
        DocumentRetrieval.revision == Document.version_number,
        Document.status == "indexed",
        Document.is_current.is_(True),
    ]
    if filters.tags is not None:
        parent_conditions.append(tag_predicate(DocumentRetrieval.tags, filters.tags))
    async with sessions() as session:
        parents = list(
            await session.execute(
                select(DocumentRetrieval, Document, parent_score)
                .join(Document, Document.id == DocumentRetrieval.doc_id)
                .where(*parent_conditions)
                .order_by(parent_score.desc(), DocumentRetrieval.doc_id)
                .limit(parent_limit)
            )
        )
        if not parents:
            # Safe partial-migration fallback: the caller can disable hierarchy
            # or continue serving the existing flat index while backfill runs.
            score = (1 - Chunk.embedding.cosine_distance(embedding)).label("score")
            rows = await session.execute(
                select(Chunk, Document, score)
                .join(Document, Document.id == Chunk.doc_id)
                .where(*_conditions(scope, filters), Chunk.embedding.is_not(None))
                .order_by(score.desc())
                .limit(limit)
            )
            return [_candidate(c, d, value) for c, d, value in rows]

        parent_scores = {str(row.doc_id): float(score) for row, _, score in parents}
        documents = {str(document.id): document for _, document, _ in parents}
        passage_score = (1 - Chunk.embedding.cosine_distance(embedding)).label(
            "passage_score"
        )
        passage_rank = func.row_number().over(
            partition_by=Chunk.doc_id,
            order_by=(passage_score.desc(), Chunk.id),
        ).label("passage_rank")
        ranked_passages = (
            select(
                Chunk.id.label("chunk_id"),
                passage_score,
                passage_rank,
            )
            .join(Document, Document.id == Chunk.doc_id)
            .where(
                *_conditions(scope, filters),
                Chunk.doc_id.in_([row.doc_id for row, _, _ in parents]),
                Chunk.embedding.is_not(None),
            )
            .subquery("ranked_passages")
        )
        passage_rows = list(
            await session.execute(
                select(Chunk, Document, ranked_passages.c.passage_score)
                .join(ranked_passages, ranked_passages.c.chunk_id == Chunk.id)
                .join(Document, Document.id == Chunk.doc_id)
                .where(
                    ranked_passages.c.passage_rank
                    <= settings.hindsight_max_passages_per_document
                )
                .order_by(ranked_passages.c.passage_score.desc(), Chunk.id)
                .limit(parent_limit * settings.hindsight_max_passages_per_document)
            )
        )
        safety_rows = []
        if settings.hindsight_hybrid_safety_lane_enabled:
            parent_ids = [row.doc_id for row, _, _ in parents]
            safety_rows = list(
                await session.execute(
                    select(Chunk, Document, passage_score)
                    .join(Document, Document.id == Chunk.doc_id)
                    .where(
                        *_conditions(scope, filters),
                        Chunk.doc_id.not_in(parent_ids),
                        Chunk.embedding.is_not(None),
                        passage_score
                        >= settings.hindsight_hybrid_safety_lane_min_score,
                    )
                    .order_by(passage_score.desc(), Chunk.id)
                    .limit(settings.hindsight_hybrid_safety_lane_limit)
                )
            )

    candidates = []
    per_document = {}
    best_passage_scores = {}
    for chunk, document, passage_value in passage_rows:
        doc_id = str(document.id)
        passage_value = float(passage_value)
        best_passage_scores[doc_id] = max(
            passage_value, best_passage_scores.get(doc_id, -1.0)
        )
        if not reliable_passage(passage_value, settings.hindsight_min_passage_score):
            continue
        used = per_document.get(doc_id, 0)
        if used >= settings.hindsight_max_passages_per_document:
            continue
        per_document[doc_id] = used + 1
        parent_value = parent_scores[doc_id]
        candidate = _candidate(
            chunk, document, 0.4 * parent_value + 0.6 * passage_value
        )
        candidate.metadata.update(
            parent_score=parent_value,
            passage_score=passage_value,
            passage_confidence="reliable",
            retrieval_level="parent_then_passage",
        )
        candidates.append(candidate)
    safety_lane_count = 0
    for chunk, document, passage_value in safety_rows:
        doc_id = str(document.id)
        used = per_document.get(doc_id, 0)
        if used >= settings.hindsight_max_passages_per_document:
            continue
        per_document[doc_id] = used + 1
        candidate = _candidate(chunk, document, float(passage_value))
        candidate.metadata.update(
            passage_score=float(passage_value),
            retrieval_level="global_safety_lane",
            safety_lane=True,
        )
        candidates.append(candidate)
        safety_lane_count += 1
    for doc_id, parent_value in parent_scores.items():
        if doc_id in per_document:
            continue
        document = documents[doc_id]
        best_passage_score = best_passage_scores.get(doc_id)
        confidence = "low" if best_passage_score is not None else "unavailable"
        candidates.append(
            RecallCandidate(
                id=f"parent:{doc_id}",
                document_id=doc_id,
                title=document.title,
                text="",
                source_text="",
                chunk_index=-1,
                source_type="upload",
                context=(
                    "Document metadata matched; available passages scored below the "
                    "reliability threshold"
                    if confidence == "low"
                    else "Document metadata matched; no passage is available"
                ),
                mentioned_at=document.updated_at.isoformat(),
                updated_at=document.updated_at.isoformat(),
                metadata={
                    "source_kind": "document_metadata",
                    "metadata_only": True,
                    "parent_score": parent_value,
                    "passage_confidence": confidence,
                    "passage_score_threshold": settings.hindsight_min_passage_score,
                    **(
                        {"best_passage_score": best_passage_score}
                        if best_passage_score is not None
                        else {}
                    ),
                    "safety_lane_candidates": safety_lane_count,
                },
                semantic_score=parent_value,
            )
        )
    return sorted(
        candidates,
        key=lambda item: (
            -(item.semantic_score or 0.0),
            bool(item.metadata.get("metadata_only")),
            item.id,
        ),
    )[:limit]


async def search_file_keywords(
    sessions, scope, query, limit, source_type, filters, *, candidate_limit, scorer
):
    filters = filters or RecallFilter()
    tokens = list(dict.fromkeys(lexical_tokens(query)))[:64]
    if _excluded(source_type, filters) or not tokens:
        return []
    # Bounded keyword candidates complement vectors for exact names/numbers.
    searchable_fields = (
        func.lower(Chunk.chunk_text),
        func.lower(Document.title),
        func.lower(func.coalesce(Document.overview, "")),
        func.lower(func.coalesce(Document.file_path, "")),
        func.lower(func.array_to_string(Document.tags, " ")),
    )
    overlap = sum(
        case(
            (
                or_(
                    *(
                        field.contains(token, autoescape=True)
                        for field in searchable_fields
                    )
                ),
                1,
            ),
            else_=0,
        )
        for token in tokens
    )
    async with sessions() as session:
        lexical_rank = func.row_number().over(
            partition_by=Chunk.doc_id,
            order_by=(overlap.desc(), Chunk.id),
        ).label("lexical_rank")
        ranked_lexical = (
            select(
                Chunk.id.label("chunk_id"),
                overlap.label("lexical_overlap"),
                lexical_rank,
            )
            .join(Document, Document.id == Chunk.doc_id)
            .where(*_conditions(scope, filters), overlap > 0)
            .subquery("ranked_lexical")
        )
        from config.settings import settings

        rows = list(
            await session.execute(
                select(Chunk, Document)
                .join(ranked_lexical, ranked_lexical.c.chunk_id == Chunk.id)
                .join(Document, Document.id == Chunk.doc_id)
                .where(
                    ranked_lexical.c.lexical_rank
                    <= settings.hindsight_max_passages_per_document
                )
                .order_by(ranked_lexical.c.lexical_overlap.desc(), Chunk.id)
                .limit(candidate_limit)
            )
        )

    fields = {
        "title": [d.title or "" for _, d in rows],
        "filename": [
            PurePath(d.file_path).name if d.file_path else "" for _, d in rows
        ],
        "overview": [d.overview or "" for _, d in rows],
        "tags": [" ".join(d.tags or []) for _, d in rows],
        "body": [c.chunk_text for c, _ in rows],
    }
    weights = {
        "title": settings.hindsight_lexical_title_weight,
        "filename": settings.hindsight_lexical_filename_weight,
        "overview": settings.hindsight_lexical_overview_weight,
        "tags": settings.hindsight_lexical_tags_weight,
        "body": settings.hindsight_lexical_body_weight,
    }
    scores, contributions = fielded_lexical_scores(query, fields, scorer, weights)
    candidates = []
    for index, ((chunk, document), score) in enumerate(zip(rows, scores, strict=True)):
        if score > 0:
            candidate = _candidate(chunk, document)
            candidate.semantic_score = None
            candidate.keyword_score = score
            candidate.metadata["lexical_field_scores"] = {
                name: round(values[index], 6)
                for name, values in contributions.items()
                if values[index] > 0
            }
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
