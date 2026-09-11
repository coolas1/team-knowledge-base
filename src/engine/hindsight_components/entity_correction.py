"""Atomic, scope-checked reassignment of explicit entity mentions."""

from __future__ import annotations

import uuid
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from .models import (
    HindsightDocumentState,
    MemoryEntity,
    MemoryEntityCorrection,
    MemoryLink,
    MemoryUnit,
    MemoryUnitEntity,
)
from .utils import document_lock_key


async def correct_entity(
    repository,
    source_entity_id: str,
    memory_ids: list[str],
    *,
    target_entity_id: str | None = None,
    reason: str,
):
    ids = sorted({uuid.UUID(value) for value in memory_ids})
    source_id = uuid.UUID(source_entity_id)
    target_id = uuid.UUID(target_entity_id) if target_entity_id else uuid.uuid4()
    if not ids or len(ids) > 100 or not reason.strip() or len(reason) > 2000:
        raise ValueError("correction requires 1–100 memories and a bounded reason")
    if source_id == target_id:
        raise ValueError("source and target entities must differ")
    async with repository._session_factory() as session:
        async with session.begin():

            async def read_memories():
                return list(
                    await session.scalars(
                        select(MemoryUnit).where(
                            MemoryUnit.id.in_(ids),
                            repository._memory_scope(),
                        )
                    )
                )

            memories = await read_memories()
            if len(memories) != len(ids):
                raise ValueError("memory does not exist")
            documents = sorted({m.document_id for m in memories})
            for document_id in documents:
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(document_id)))
                )
            memories = await read_memories()
            if len(memories) != len(ids):
                raise ValueError("memory no longer exists")
            entities = list(
                await session.scalars(
                    select(MemoryEntity)
                    .where(
                        MemoryEntity.id.in_([source_id, target_id]),
                        MemoryEntity.bank_id == repository.scope.bank_id,
                    )
                    .order_by(MemoryEntity.id)
                    .with_for_update()
                )
            )
            source = next((e for e in entities if e.id == source_id), None)
            if source is None:
                raise ValueError("entity does not exist")
            mentions = list(
                await session.scalars(
                    select(MemoryUnitEntity).where(
                        MemoryUnitEntity.memory_id.in_(ids),
                        MemoryUnitEntity.entity_id == source_id,
                        MemoryUnitEntity.bank_id == repository.scope.bank_id,
                    )
                )
            )
            if len(mentions) != len(ids):
                raise ValueError(
                    "selected memories do not all mention the source entity"
                )
            if target_entity_id:
                visible_target = await session.scalar(
                    select(MemoryUnitEntity.entity_id)
                    .join(
                        MemoryUnit,
                        MemoryUnit.id == MemoryUnitEntity.memory_id,
                    )
                    .where(
                        MemoryUnitEntity.entity_id == target_id,
                        repository._memory_scope(),
                    )
                    .limit(1)
                )
                if visible_target is None:
                    raise ValueError("target entity does not exist")
            else:
                session.add(
                    MemoryEntity(
                        id=target_id,
                        bank_id=repository.scope.bank_id,
                        canonical_name=source.canonical_name,
                        normalized_name=source.normalized_name,
                        entity_type=source.entity_type,
                        identity_key=str(target_id),
                    )
                )
                await session.flush()
            for mention in mentions:
                await session.execute(
                    insert(MemoryUnitEntity)
                    .values(
                        memory_id=mention.memory_id,
                        entity_id=target_id,
                        bank_id=repository.scope.bank_id,
                        original_name=mention.original_name,
                        aliases=list(mention.aliases),
                        role=mention.role,
                    )
                    .on_conflict_do_nothing()
                )
            await session.execute(
                delete(MemoryUnitEntity).where(
                    MemoryUnitEntity.memory_id.in_(ids),
                    MemoryUnitEntity.entity_id == source_id,
                )
            )
            correction_id = uuid.uuid4()
            await session.execute(
                update(HindsightDocumentState)
                .where(
                    HindsightDocumentState.document_id.in_(documents),
                    HindsightDocumentState.bank_id == repository.scope.bank_id,
                )
                .values(revision=HindsightDocumentState.revision + 1)
            )
            impacted = await repository._dependent_graph_documents(
                session, ids, exclude_document_id=documents[0]
            )
            for memory in memories:
                metadata = dict(memory.metadata_json or {})
                metadata["resolved_entities"] = [
                    {
                        **entry,
                        "id": str(target_id),
                        "decision": "corrected",
                        "existing": True,
                    }
                    if entry.get("id") == str(source_id)
                    else entry
                    for entry in metadata.get("resolved_entities", [])
                ]
                metadata["entity_correction_id"] = str(correction_id)
                memory.metadata_json = metadata
            # Replace affected entity edges using actual remaining mention ownership.
            visible_ids = select(MemoryUnit.id).where(repository._memory_scope())
            await session.execute(
                delete(MemoryLink).where(
                    MemoryLink.link_type == "entity",
                    MemoryLink.bank_id == repository.scope.bank_id,
                    or_(
                        MemoryLink.source_memory_id.in_(ids),
                        MemoryLink.target_memory_id.in_(ids),
                    ),
                    MemoryLink.source_memory_id.in_(visible_ids),
                    MemoryLink.target_memory_id.in_(visible_ids),
                )
            )
            selected_entities = select(MemoryUnitEntity.entity_id).where(
                MemoryUnitEntity.memory_id.in_(ids)
            )
            peers = (
                await session.execute(
                    select(MemoryUnitEntity.entity_id, MemoryUnitEntity.memory_id)
                    .join(MemoryUnit, MemoryUnit.id == MemoryUnitEntity.memory_id)
                    .where(
                        MemoryUnitEntity.entity_id.in_(selected_entities),
                        repository._memory_scope(),
                    )
                )
            ).all()
            by_entity = {}
            for entity_id, memory_id in peers:
                by_entity.setdefault(entity_id, set()).add(memory_id)
            if peers:
                impacted.update(
                    await session.scalars(
                        select(MemoryUnit.document_id).where(
                            MemoryUnit.id.in_({memory_id for _, memory_id in peers}),
                            repository._memory_scope(),
                        )
                    )
                )
            for members in by_entity.values():
                for changed in members.intersection(ids):
                    for peer in members - {changed}:
                        left, right = sorted((changed, peer))
                        await session.execute(
                            insert(MemoryLink)
                            .values(
                                source_memory_id=left,
                                target_memory_id=right,
                                link_type="entity",
                                weight=1.0,
                                bank_id=repository.scope.bank_id,
                            )
                            .on_conflict_do_nothing()
                        )
            session.add(
                MemoryEntityCorrection(
                    id=correction_id,
                    bank_id=repository.scope.bank_id,
                    source_entity_id=source_id,
                    target_entity_id=target_id,
                    memory_ids=ids,
                    reason=reason.strip(),
                )
            )
            for document_id in sorted(set(documents) | impacted):
                repository._enqueue_graph_event(session, document_id, "replace")
    return {
        "correction_id": str(correction_id),
        "source_entity_id": str(source_id),
        "target_entity_id": str(target_id),
        "memory_ids": [str(value) for value in ids],
    }
