"""PostgreSQL implementation of the Hindsight memory repository port."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.components.store.models import Document

from .models import (
    HindsightDocumentState,
    MemoryEntity,
    MemoryLink,
    MemoryProfile as MemoryProfileRow,
    MemoryUnit,
    MemoryUnitEntity,
    MentalModel as MentalModelRow,
)
from .types import (
    MemoryProfile,
    MentalModel,
    RecallCandidate,
    ReflectionContext,
    RetainPlan,
)
from .utils import document_lock_key, lexical_tokens, normalize_entity

SessionFactory = Callable[[], Any]


class PostgresMemoryRepository:
    """Own only Hindsight memory rows while reusing TKB documents/sessions."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def replace_document(self, plan: RetainPlan) -> None:
        document_id = uuid.UUID(plan.document_id)
        async with self._session_factory() as session:
            async with session.begin():
                if await session.get(Document, document_id) is None:
                    raise ValueError(f"document does not exist: {plan.document_id}")
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(document_id)))
                )
                old_ids = list(
                    await session.scalars(
                        select(MemoryUnit.id).where(
                            MemoryUnit.document_id == document_id
                        )
                    )
                )
                if old_ids:
                    await session.execute(
                        delete(MemoryUnit).where(
                            MemoryUnit.memory_type == "observation",
                            MemoryUnit.source_memory_ids.overlap(old_ids),
                        )
                    )
                await session.execute(
                    delete(MemoryUnit).where(MemoryUnit.document_id == document_id)
                )
                await self._insert_memories(session, plan)
                await self._insert_links(session, plan)
                await session.execute(
                    delete(MemoryEntity).where(
                        ~select(MemoryUnitEntity.entity_id)
                        .where(MemoryUnitEntity.entity_id == MemoryEntity.id)
                        .exists()
                    )
                )
                await session.execute(
                    insert(HindsightDocumentState)
                    .values(
                        document_id=document_id,
                        status="indexed",
                        error_msg=None,
                        memory_count=len(plan.memories),
                        link_count=len(plan.links),
                    )
                    .on_conflict_do_update(
                        index_elements=[HindsightDocumentState.document_id],
                        set_={
                            "status": "indexed",
                            "error_msg": None,
                            "memory_count": len(plan.memories),
                            "link_count": len(plan.links),
                            "updated_at": func.now(),
                        },
                    )
                )

    async def set_document_state(
        self,
        document_id: str,
        status: str,
        *,
        error_msg: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                insert(HindsightDocumentState)
                .values(
                    document_id=uuid.UUID(document_id),
                    status=status,
                    error_msg=error_msg,
                )
                .on_conflict_do_update(
                    index_elements=[HindsightDocumentState.document_id],
                    set_={
                        "status": status,
                        "error_msg": error_msg,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.commit()

    async def delete_document(self, document_id: str) -> None:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(uid)))
                )
                memory_ids = list(
                    await session.scalars(
                        select(MemoryUnit.id).where(MemoryUnit.document_id == uid)
                    )
                )
                if memory_ids:
                    await session.execute(
                        delete(MemoryUnit).where(
                            MemoryUnit.memory_type == "observation",
                            MemoryUnit.source_memory_ids.overlap(memory_ids),
                        )
                    )
                await session.execute(
                    delete(MemoryUnit).where(MemoryUnit.document_id == uid)
                )
                await session.execute(
                    delete(HindsightDocumentState).where(
                        HindsightDocumentState.document_id == uid
                    )
                )
                await session.execute(
                    delete(MemoryEntity).where(
                        ~select(MemoryUnitEntity.entity_id)
                        .where(MemoryUnitEntity.entity_id == MemoryEntity.id)
                        .exists()
                    )
                )

    async def _insert_memories(self, session: AsyncSession, plan: RetainPlan) -> None:
        for draft in plan.memories:
            row = MemoryUnit(
                id=uuid.UUID(draft.id),
                document_id=uuid.UUID(draft.document_id),
                chunk_index=draft.chunk_index,
                memory_index=draft.memory_index,
                memory_type=draft.memory_type,
                text=draft.text,
                source_text=draft.source_text,
                context=draft.context,
                embedding=draft.embedding,
                occurred_start=draft.occurred_start,
                occurred_end=draft.occurred_end,
                confidence=draft.confidence,
                is_source_chunk=draft.is_source_chunk,
                location=draft.location,
                proof_count=max(1, len(draft.source_memory_ids)),
                source_memory_ids=[uuid.UUID(item) for item in draft.source_memory_ids],
                tags=list(draft.tags),
                metadata_json=dict(draft.metadata),
            )
            session.add(row)
            for entity_name in draft.entities:
                normalized = normalize_entity(entity_name)
                if not normalized:
                    continue
                await session.execute(
                    insert(MemoryEntity)
                    .values(
                        canonical_name=entity_name.strip(),
                        normalized_name=normalized,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[MemoryEntity.normalized_name]
                    )
                )
                entity_id = await session.scalar(
                    select(MemoryEntity.id).where(
                        MemoryEntity.normalized_name == normalized
                    )
                )
                if entity_id is None:
                    raise RuntimeError(f"failed to persist entity: {entity_name}")
                await session.execute(
                    insert(MemoryUnitEntity)
                    .values(memory_id=row.id, entity_id=entity_id)
                    .on_conflict_do_nothing()
                )
        await session.flush()

    async def _insert_links(self, session: AsyncSession, plan: RetainPlan) -> None:
        planned_ids = {uuid.UUID(memory.id) for memory in plan.memories}
        external_ids = {
            uuid.UUID(link.target_memory_id)
            for link in plan.links
            if uuid.UUID(link.target_memory_id) not in planned_ids
        }
        existing_external = (
            set(
                await session.scalars(
                    select(MemoryUnit.id).where(MemoryUnit.id.in_(external_ids))
                )
            )
            if external_ids
            else set()
        )
        valid_ids = planned_ids | existing_external
        for link in plan.links:
            source_id = uuid.UUID(link.source_memory_id)
            target_id = uuid.UUID(link.target_memory_id)
            if source_id not in valid_ids or target_id not in valid_ids:
                continue
            await session.execute(
                insert(MemoryLink)
                .values(
                    source_memory_id=source_id,
                    target_memory_id=target_id,
                    link_type=link.link_type,
                    weight=link.weight,
                )
                .on_conflict_do_nothing()
            )

    async def semantic_neighbors(
        self,
        embedding: list[float],
        *,
        exclude_document_id: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        score = (1 - MemoryUnit.embedding.cosine_distance(embedding)).label("score")
        async with self._session_factory() as session:
            rows = await session.execute(
                select(MemoryUnit.id, score)
                .where(
                    MemoryUnit.document_id != uuid.UUID(exclude_document_id),
                    MemoryUnit.state == "active",
                    MemoryUnit.embedding.is_not(None),
                )
                .order_by(score.desc())
                .limit(limit)
            )
        return [(str(memory_id), float(value)) for memory_id, value in rows]

    async def semantic_search(
        self, embedding: list[float], limit: int
    ) -> list[RecallCandidate]:
        score = (1 - MemoryUnit.embedding.cosine_distance(embedding)).label("score")
        async with self._session_factory() as session:
            rows = await session.execute(
                select(MemoryUnit, Document, score)
                .join(Document, Document.id == MemoryUnit.document_id)
                .where(
                    MemoryUnit.state == "active",
                    MemoryUnit.embedding.is_not(None),
                    Document.status == "indexed",
                )
                .order_by(score.desc())
                .limit(limit)
            )
        return [
            self._candidate(unit, document, semantic_score=float(value))
            for unit, document, value in rows
        ]

    async def keyword_search(self, query: str, limit: int) -> list[RecallCandidate]:
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(MemoryUnit, Document)
                        .join(Document, Document.id == MemoryUnit.document_id)
                        .where(
                            MemoryUnit.state == "active",
                            Document.status == "indexed",
                        )
                    )
                ).all()
            )
        scores = self._bm25(query, [unit.text for unit, _ in rows])
        ranked = sorted(
            (
                self._candidate(unit, document, keyword_score=score)
                for (unit, document), score in zip(rows, scores, strict=True)
                if score > 0
            ),
            key=lambda item: item.keyword_score or 0,
            reverse=True,
        )
        return ranked[:limit]

    async def graph_search(
        self, entities: list[str], limit: int
    ) -> list[RecallCandidate]:
        normalized = [normalize_entity(item) for item in entities]
        normalized = [item for item in normalized if item]
        if not normalized:
            return []
        async with self._session_factory() as session:
            direct_rows = await session.execute(
                select(
                    MemoryUnit,
                    Document,
                    func.count(MemoryEntity.id).label("score"),
                )
                .join(MemoryUnitEntity, MemoryUnitEntity.memory_id == MemoryUnit.id)
                .join(MemoryEntity, MemoryEntity.id == MemoryUnitEntity.entity_id)
                .join(Document, Document.id == MemoryUnit.document_id)
                .where(
                    MemoryUnit.state == "active",
                    Document.status == "indexed",
                    or_(
                        *[
                            MemoryEntity.normalized_name.contains(item)
                            for item in normalized
                        ]
                    ),
                )
                .group_by(MemoryUnit.id, Document.id)
                .order_by(text("score DESC"))
                .limit(limit)
            )
            direct = list(direct_rows.all())
            seed_ids = [unit.id for unit, _, _ in direct]
            expanded_scores: defaultdict[uuid.UUID, float] = defaultdict(float)
            if seed_ids:
                link_rows = await session.scalars(
                    select(MemoryLink).where(
                        or_(
                            MemoryLink.source_memory_id.in_(seed_ids),
                            MemoryLink.target_memory_id.in_(seed_ids),
                        )
                    )
                )
                for link in link_rows:
                    target_id = (
                        link.target_memory_id
                        if link.source_memory_id in seed_ids
                        else link.source_memory_id
                    )
                    expanded_scores[target_id] = max(
                        expanded_scores[target_id], float(link.weight) * 0.8
                    )
            expanded = []
            if expanded_scores:
                expanded = list(
                    (
                        await session.execute(
                            select(MemoryUnit, Document)
                            .join(Document, Document.id == MemoryUnit.document_id)
                            .where(
                                MemoryUnit.id.in_(expanded_scores),
                                MemoryUnit.state == "active",
                                Document.status == "indexed",
                            )
                        )
                    ).all()
                )

        best: dict[str, RecallCandidate] = {}
        for unit, document, score in direct:
            item = self._candidate(unit, document, graph_score=float(score))
            best[item.id] = item
        for unit, document in expanded:
            item = self._candidate(unit, document, graph_score=expanded_scores[unit.id])
            if item.id not in best or (item.graph_score or 0) > (
                best[item.id].graph_score or 0
            ):
                best[item.id] = item
        return sorted(
            best.values(), key=lambda item: item.graph_score or 0, reverse=True
        )[:limit]

    async def temporal_search(
        self,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[RecallCandidate]:
        if start is None and end is None:
            return []
        conditions = [MemoryUnit.occurred_start.is_not(None)]
        if start is not None:
            conditions.append(
                or_(MemoryUnit.occurred_end.is_(None), MemoryUnit.occurred_end >= start)
            )
        if end is not None:
            conditions.append(MemoryUnit.occurred_start <= end)
        async with self._session_factory() as session:
            rows = await session.execute(
                select(MemoryUnit, Document)
                .join(Document, Document.id == MemoryUnit.document_id)
                .where(
                    *conditions,
                    MemoryUnit.state == "active",
                    Document.status == "indexed",
                )
                .order_by(MemoryUnit.occurred_start.desc())
                .limit(limit)
            )
        return [
            self._candidate(unit, document, temporal_score=1.0)
            for unit, document in rows
        ]

    async def entity_states(self, memory_ids: list[str]) -> dict[str, Any]:
        if not memory_ids:
            return {}
        ids = [uuid.UUID(item) for item in memory_ids]
        async with self._session_factory() as session:
            rows = await session.execute(
                select(MemoryEntity, MemoryUnit)
                .join(MemoryUnitEntity, MemoryUnitEntity.entity_id == MemoryEntity.id)
                .join(MemoryUnit, MemoryUnit.id == MemoryUnitEntity.memory_id)
                .where(MemoryUnit.id.in_(ids))
            )
        states: dict[str, Any] = {}
        for entity, unit in rows:
            state = states.setdefault(
                entity.canonical_name,
                {
                    "id": str(entity.id),
                    "canonical_name": entity.canonical_name,
                    "observations": [],
                },
            )
            if unit.memory_type == "observation":
                state["observations"].append(
                    {"text": unit.text, "mentioned_at": unit.mentioned_at.isoformat()}
                )
        return states

    async def reflection_context(
        self, query: str, query_embedding: list[float]
    ) -> ReflectionContext:
        async with self._session_factory() as session:
            models = list((await session.scalars(select(MentalModelRow))).all())
            profile = await session.get(MemoryProfileRow, "default")
        return ReflectionContext(
            mental_models=[
                MentalModel(
                    id=row.id,
                    name=row.name,
                    description=row.description,
                    summary=row.summary,
                    is_directive=row.is_directive,
                    trigger=row.trigger,
                    embedding=list(row.embedding)
                    if row.embedding is not None
                    else None,
                    source_memory_ids=[str(item) for item in row.source_memory_ids],
                )
                for row in models
            ],
            profile=MemoryProfile(
                background=profile.background if profile else "",
                skepticism=profile.skepticism if profile else 3,
                literalism=profile.literalism if profile else 3,
                empathy=profile.empathy if profile else 3,
            ),
        )

    @staticmethod
    def _candidate(
        unit: MemoryUnit, document: Document, **scores: float
    ) -> RecallCandidate:
        return RecallCandidate(
            id=str(unit.id),
            document_id=str(unit.document_id),
            title=document.title,
            text=unit.text,
            source_text=unit.source_text,
            chunk_index=unit.chunk_index,
            memory_type=unit.memory_type,
            context=unit.context,
            occurred_start=(
                unit.occurred_start.isoformat() if unit.occurred_start else None
            ),
            occurred_end=unit.occurred_end.isoformat() if unit.occurred_end else None,
            metadata=dict(unit.metadata_json or {}),
            source_memory_ids=[str(item) for item in unit.source_memory_ids],
            embedding=list(unit.embedding) if unit.embedding is not None else None,
            **scores,
        )

    @staticmethod
    def _bm25(query: str, documents: list[str]) -> list[float]:
        query_tokens = lexical_tokens(query)
        tokenized = [lexical_tokens(document) for document in documents]
        if not query_tokens or not tokenized:
            return [0.0] * len(documents)
        average_length = sum(map(len, tokenized)) / len(tokenized) or 1
        frequencies = {
            token: sum(token in set(tokens) for tokens in tokenized)
            for token in set(query_tokens)
        }
        scores: list[float] = []
        k1, b = 1.5, 0.75
        for tokens in tokenized:
            score = 0.0
            for token in query_tokens:
                frequency = tokens.count(token)
                if not frequency:
                    continue
                document_frequency = frequencies[token]
                inverse_df = math.log(
                    1
                    + (len(tokenized) - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(tokens) / average_length
                )
                score += inverse_df * frequency * (k1 + 1) / denominator
            scores.append(score)
        return scores
