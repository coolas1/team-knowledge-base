"""PostgreSQL implementation of the Hindsight memory repository port."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import Text, bindparam, case, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.components.store.models import Document
from src.engine.components.store.scope import scope_predicate, tag_predicate
from src.engine.scope import MemoryScope

from .models import (
    ConsolidationFactEvent,
    ConsolidationJob,
    ConversationMemorySource,
    FactTombstone,
    HindsightGraphOutbox,
    HindsightDocumentState,
    MemoryEntity,
    MemoryLink,
    MemoryProfile as MemoryProfileRow,
    MemoryUnit,
    MemoryUnitEntity,
    MentalModel as MentalModelRow,
    MentalModelRefreshJob,
    ObservationEvidence,
    ObservationHistory,
    ObservationRecord,
)
from .types import (
    DocumentMemoryState,
    MemoryProfile,
    MentalModel,
    RecallCandidate,
    RecallFilter,
    ReflectionContext,
    RetainPlan,
)
from .graph_types import (
    MemoryGraphDocument,
    MemoryGraphEntity,
    MemoryGraphLink as MemoryGraphLinkProjection,
    MemoryGraphMemory,
    MemoryGraphMention,
    MemoryGraphProjection,
)
from .utils import document_lock_key, lexical_tokens, normalize_entity

SessionFactory = Callable[[], Any]


class PostgresMemoryRepository:
    """Own only Hindsight memory rows while reusing TKB documents/sessions."""

    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        *,
        keyword_index_enabled: bool = False,
        keyword_candidate_limit: int = 300,
        scope: MemoryScope | None = None,
        retention_lease: tuple[str, str] | None = None,
        consolidation_enabled: bool = False,
    ) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory
        if keyword_candidate_limit < 1:
            raise ValueError("keyword_candidate_limit must be greater than zero")
        self._keyword_index_enabled = keyword_index_enabled
        self._keyword_candidate_limit = keyword_candidate_limit
        self.scope = scope or MemoryScope()
        self._retention_lease = retention_lease
        self._consolidation_enabled = consolidation_enabled

    def with_scope(self, scope: MemoryScope) -> PostgresMemoryRepository:
        return PostgresMemoryRepository(
            self._session_factory,
            keyword_index_enabled=self._keyword_index_enabled,
            keyword_candidate_limit=self._keyword_candidate_limit,
            scope=scope,
            retention_lease=self._retention_lease,
            consolidation_enabled=self._consolidation_enabled,
        )

    def with_lease(self, document_id: str, lease_token: str):
        return PostgresMemoryRepository(
            self._session_factory,
            keyword_index_enabled=self._keyword_index_enabled,
            keyword_candidate_limit=self._keyword_candidate_limit,
            scope=self.scope,
            retention_lease=(document_id, lease_token),
            consolidation_enabled=self._consolidation_enabled,
        )

    async def prepare_file_retention(self, value):
        from dataclasses import asdict, replace
        from src.engine.components.file_summary import FileSummaryManager
        from .types import RetentionRevisionConflict

        async with self._session_factory() as session:
            document = await session.scalar(
                select(Document).where(
                    Document.id == uuid.UUID(value.document_id), self._document_scope()
                )
            )
            if document is None:
                raise ValueError("document is not visible")
            if document.file_type == "conversation":
                return value
            if value.content != document.raw_text:
                raise RetentionRevisionConflict(
                    "file source changed before summary retention"
                )
            title, source = document.title, document.raw_text
        summary = await FileSummaryManager(
            self._session_factory, scope=self.scope
        ).prepare(value.document_id, source, title)
        return replace(
            value,
            content=summary.text,
            title=title,
            update_mode="replace",
            metadata={
                **value.metadata,
                "file_summary": {
                    **asdict(summary.identity),
                    "key": summary.identity.key,
                    "coverage": summary.coverage,
                },
            },
        )

    async def retention_input(self, document_id: str):
        from .types import RetainInput
        from datetime import datetime

        async with self._session_factory() as session:
            document = await session.scalar(
                select(Document).where(
                    Document.id == uuid.UUID(document_id), self._document_scope()
                )
            )
            if document is None:
                raise ValueError("document does not exist")
            if document.file_type == "conversation":
                raise ValueError("conversation extraction must use the durable queue")
            state = await session.get(HindsightDocumentState, document.id)
            saved = dict(state.source_context or {}) if state else {}
            return RetainInput(
                document_id=str(document.id),
                expected_revision=state.revision if state else 0,
                title=document.title,
                content=document.raw_text,
                file_type=document.file_type,
                tags=tuple(document.tags or []),
                agent_name=saved.get("agent_name", self.scope.agent_name),
                source_timestamp=datetime.fromisoformat(saved["source_timestamp"])
                if saved.get("source_timestamp")
                else None,
                reference_timezone=saved.get("reference_timezone", "UTC"),
                policy_version=self.scope.policy_version,
                speakers=saved.get(
                    "speakers",
                    {
                        "user": self.scope.subject_id,
                        "assistant": self.scope.agent_name,
                    },
                ),
            )

    async def _check_retention_lease(self, session, document_id):
        if self._retention_lease is None:
            return
        from .models import ConversationMemorySource
        from .types import RetentionLeaseLost

        owner, token = self._retention_lease
        if str(document_id) != owner:
            raise RetentionLeaseLost("retention lease belongs to a different document")
        source = await session.scalar(
            select(ConversationMemorySource)
            .where(
                ConversationMemorySource.document_id == uuid.UUID(owner),
                ConversationMemorySource.bank_id == self.scope.bank_id,
                ConversationMemorySource.lease_token == uuid.UUID(token),
                ConversationMemorySource.status == "processing",
                ConversationMemorySource.lease_expires_at > func.clock_timestamp(),
            )
            .with_for_update()
        )
        if source is None:
            raise RetentionLeaseLost("retention lease is no longer valid")

    def _memory_scope(self):
        return scope_predicate(
            MemoryUnit.bank_id, MemoryUnit.scope_tags, self.scope
        ) & MemoryUnit.document_id.in_(
            select(Document.id).where(self._document_scope()).correlate(None)
        )

    def _document_scope(self):
        return scope_predicate(Document.bank_id, Document.tags, self.scope)

    async def retention_revision(self, document_id: str) -> int:
        async with self._session_factory() as session:
            visible = await session.scalar(
                select(Document.id).where(
                    Document.id == uuid.UUID(document_id), self._document_scope()
                )
            )
            if visible is None:
                raise ValueError("document does not exist")
            return int(
                await session.scalar(
                    select(HindsightDocumentState.revision).where(
                        HindsightDocumentState.document_id == visible
                    )
                )
                or 0
            )

    async def retention_content_snapshot(self, document_id: str) -> tuple[int, dict]:
        """Read content and its CAS revision in the same scoped SQL statement."""
        async with self._session_factory() as session:
            # Legacy reconstruction spans state and source rows. The same lock
            # used by publication keeps both reads at one document revision.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        document_lock_key(uuid.UUID(document_id))
                    )
                )
            )
            row = (
                await session.execute(
                    select(
                        HindsightDocumentState.revision,
                        HindsightDocumentState.content_snapshot,
                    )
                    .select_from(Document)
                    .outerjoin(
                        HindsightDocumentState,
                        HindsightDocumentState.document_id == Document.id,
                    )
                    .where(
                        Document.id == uuid.UUID(document_id), self._document_scope()
                    )
                )
            ).one_or_none()
            if row is None:
                raise ValueError("document does not exist")
            if not row[1]:
                document = await session.get(Document, uuid.UUID(document_id))
                memories = list(
                    await session.scalars(
                        select(MemoryUnit)
                        .where(
                            MemoryUnit.document_id == document.id, self._memory_scope()
                        )
                        .order_by(MemoryUnit.chunk_index, MemoryUnit.memory_index)
                    )
                )
                if memories:
                    from .retention_snapshot import legacy_content_snapshot

                    state = await session.get(HindsightDocumentState, document.id)
                    return int(row[0] or 0), legacy_content_snapshot(
                        document,
                        memories,
                        dict(state.source_context or {}) if state else {},
                    )
            return int(row[0] or 0), dict(row[1] or {})

    async def retention_extraction_cache(self, document_id: str) -> dict:
        async with self._session_factory() as session:
            visible = await session.scalar(
                select(Document.id).where(
                    Document.id == uuid.UUID(document_id), self._document_scope()
                )
            )
            if visible is None:
                raise ValueError("document does not exist")
            cache = await session.scalar(
                select(HindsightDocumentState.extraction_cache).where(
                    HindsightDocumentState.document_id == visible
                )
            )
            return dict(cache or {})

    async def retention_request_result(
        self, document_id: str, request_id: str, request_hash: str
    ):
        from .models import RetentionRequest
        from .types import RetentionRequestConflict

        async with self._session_factory() as session:
            visible = await session.scalar(
                select(Document.id).where(
                    Document.id == uuid.UUID(document_id), self._document_scope()
                )
            )
            if visible is None:
                raise ValueError("document does not exist")
            row = await session.get(RetentionRequest, (visible, request_id))
            if row is None:
                return None
            if row.bank_id != self.scope.bank_id or row.request_hash != request_hash:
                raise RetentionRequestConflict("retention request content conflict")
            return dict(row.result_payload)

    async def entity_candidates(
        self,
        names: tuple[str, ...],
        *,
        limit: int = 10,
        exclude_document_id: str | None = None,
    ):
        from sqlalchemy import or_
        from .entity_resolver import EntityCandidate

        if not 1 <= limit <= 100:
            raise ValueError("entity candidate limit must be between 1 and 100")
        normalized = tuple(
            dict.fromkeys(normalize_entity(n) for n in names if normalize_entity(n))
        )
        if not normalized:
            return []
        # Alias evidence belongs to visible facts, never to a global entity profile.
        aliases = MemoryUnitEntity.aliases
        candidate_scope = [
            self._memory_scope(),
            MemoryEntity.bank_id == self.scope.bank_id,
            or_(
                MemoryEntity.normalized_name.in_(normalized),
                func.lower(MemoryUnitEntity.original_name).in_(normalized),
                aliases.overlap(list(normalized)),
            ),
        ]
        if exclude_document_id is not None:
            candidate_scope.append(
                MemoryUnit.document_id != uuid.UUID(exclude_document_id)
            )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MemoryEntity, MemoryUnit.text, aliases.label("aliases"))
                    .join(
                        MemoryUnitEntity, MemoryUnitEntity.entity_id == MemoryEntity.id
                    )
                    .join(MemoryUnit, MemoryUnit.id == MemoryUnitEntity.memory_id)
                    .where(*candidate_scope)
                    .order_by(MemoryEntity.id, MemoryUnit.id)
                    .limit(limit * 3)
                )
            ).all()
        collected = {}
        for entity, evidence, source_aliases in rows:
            key = str(entity.id)
            entry = collected.setdefault(
                key, {"name": entity.canonical_name, "aliases": [], "evidence": []}
            )
            if isinstance(source_aliases, list):
                entry["aliases"].extend(a for a in source_aliases if isinstance(a, str))
            if len(entry["evidence"]) < 3:
                entry["evidence"].append(evidence)
        return [
            EntityCandidate(
                key,
                data["name"],
                tuple(dict.fromkeys(data["aliases"])),
                tuple(data["evidence"]),
            )
            for key, data in list(collected.items())[:limit]
        ]

    async def replace_document(self, plan: RetainPlan) -> None:
        if any(memory.document_id != plan.document_id for memory in plan.memories):
            raise ValueError("memory draft belongs to a different document")
        document_id = uuid.UUID(plan.document_id)
        async with self._session_factory() as session:
            async with session.begin():
                await self._check_retention_lease(session, document_id)
                document = await session.get(Document, document_id)
                if document is None or not self.scope.permits(
                    getattr(document, "bank_id", None) or "default-team",
                    getattr(document, "tags", None) or [],
                ):
                    raise ValueError(f"document does not exist: {plan.document_id}")
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(document_id)))
                )
                from .models import RetentionRequest
                from .types import RetentionRequestConflict

                if plan.request_id:
                    prior = await session.get(
                        RetentionRequest, (document_id, plan.request_id)
                    )
                    if prior is not None:
                        if (
                            prior.bank_id != self.scope.bank_id
                            or prior.request_hash != plan.request_hash
                        ):
                            raise RetentionRequestConflict(
                                "retention request content conflict"
                            )
                        plan.result_payload = dict(prior.result_payload)
                        plan.revision = plan.result_payload["revision"]
                        return
                from .types import RetentionRevisionConflict

                current_revision = int(
                    await session.scalar(
                        select(HindsightDocumentState.revision).where(
                            HindsightDocumentState.document_id == document_id
                        )
                    )
                    or 0
                )
                if (
                    plan.expected_revision is not None
                    and plan.expected_revision != current_revision
                ):
                    raise RetentionRevisionConflict("retention revision conflict")
                plan.revision = current_revision + 1
                if plan.result_payload:
                    plan.result_payload["revision"] = plan.revision
                targets = {
                    item["id"]
                    for memory in plan.memories
                    for item in memory.metadata.get("resolved_entities", [])
                    if item.get("existing")
                }
                for target in sorted(targets):
                    visible = await session.scalar(
                        select(MemoryEntity.id)
                        .where(
                            MemoryEntity.id == uuid.UUID(target),
                            MemoryEntity.bank_id == self.scope.bank_id,
                            MemoryEntity.id.in_(
                                select(MemoryUnitEntity.entity_id)
                                .join(
                                    MemoryUnit,
                                    MemoryUnit.id == MemoryUnitEntity.memory_id,
                                )
                                .where(self._memory_scope())
                            ),
                        )
                        .with_for_update()
                    )
                    if visible is None:
                        raise ValueError("resolved entity is no longer visible")
                old_rows = list(
                    await session.scalars(
                        select(MemoryUnit).where(
                            MemoryUnit.document_id == document_id,
                            self._memory_scope(),
                            ~select(ObservationRecord.memory_id)
                            .where(ObservationRecord.memory_id == MemoryUnit.id)
                            .exists(),
                        )
                    )
                )
                old_ids = [row.id for row in old_rows]
                retained_ids = {uuid.UUID(memory.id) for memory in plan.memories} & set(
                    old_ids
                )
                corrected_ids = {
                    row.id
                    for row in old_rows
                    if "entity_correction_id" in (row.metadata_json or {})
                }
                refreshable_entity_ids = retained_ids - corrected_ids
                removed_ids = list(set(old_ids) - retained_ids)
                impacted_documents = await self._dependent_graph_documents(
                    session,
                    removed_ids,
                    exclude_document_id=document_id,
                )
                if removed_ids:
                    await self._invalidate_observation_evidence(
                        session, removed_ids, reason="source_replaced"
                    )
                    await session.execute(
                        delete(MemoryUnit).where(
                            MemoryUnit.memory_type == "observation",
                            self._memory_scope(),
                            MemoryUnit.source_memory_ids.overlap(removed_ids),
                            ~select(ObservationRecord.memory_id)
                            .where(ObservationRecord.memory_id == MemoryUnit.id)
                            .exists(),
                        )
                    )
                await session.execute(
                    delete(MemoryUnit).where(
                        MemoryUnit.document_id == document_id,
                        self._memory_scope(),
                        MemoryUnit.id.in_(removed_ids),
                    )
                )
                if refreshable_entity_ids:
                    await session.execute(
                        delete(MemoryLink).where(
                            MemoryLink.bank_id == self.scope.bank_id,
                            MemoryLink.link_type == "entity",
                            MemoryLink.source_memory_id.in_(refreshable_entity_ids),
                        )
                    )
                await self._insert_memories(
                    session,
                    plan,
                    scope_tags=getattr(document, "tags", None) or [],
                    retained_ids=retained_ids,
                )
                await self._insert_links(session, plan)
                await self._enqueue_consolidation_changes(
                    session,
                    document_id=document_id,
                    document_revision=plan.revision,
                    scope_tags=tuple(getattr(document, "tags", None) or ()),
                    added=[
                        memory
                        for memory in plan.memories
                        if uuid.UUID(memory.id) not in retained_ids
                        and not memory.is_source_chunk
                        and memory.memory_type in {"world", "experience"}
                    ],
                    removed=[
                        row
                        for row in old_rows
                        if row.id in removed_ids
                        and not row.is_source_chunk
                        and row.memory_type in {"world", "experience"}
                    ],
                )
                await session.execute(
                    delete(MemoryEntity).where(
                        MemoryEntity.bank_id == self.scope.bank_id,
                        ~select(MemoryUnitEntity.entity_id)
                        .where(MemoryUnitEntity.entity_id == MemoryEntity.id)
                        .exists(),
                    )
                )
                await session.execute(
                    insert(HindsightDocumentState)
                    .values(
                        document_id=document_id,
                        operation_id=document_id,
                        revision=plan.revision,
                        stage_results=dict(plan.stage_results),
                        source_context=dict(plan.source_context),
                        extraction_cache=dict(plan.extraction_cache),
                        content_snapshot=dict(plan.content_snapshot),
                        status="degraded"
                        if plan.extraction_status == "degraded"
                        else "indexed",
                        bank_id=self.scope.bank_id,
                        error_msg=None,
                        memory_count=len(plan.memories),
                        link_count=len(plan.links),
                    )
                    .on_conflict_do_update(
                        index_elements=[HindsightDocumentState.document_id],
                        set_={
                            "revision": plan.revision,
                            "stage_results": dict(plan.stage_results),
                            "source_context": dict(plan.source_context),
                            "extraction_cache": dict(plan.extraction_cache),
                            "content_snapshot": dict(plan.content_snapshot),
                            "status": "degraded"
                            if plan.extraction_status == "degraded"
                            else "indexed",
                            "error_msg": None,
                            "memory_count": len(plan.memories),
                            "link_count": len(plan.links),
                            "updated_at": func.now(),
                        },
                    )
                )
                self._enqueue_graph_event(session, document_id, "replace")
                if plan.request_id:
                    session.add(
                        RetentionRequest(
                            document_id=document_id,
                            bank_id=self.scope.bank_id,
                            request_id=plan.request_id,
                            request_hash=plan.request_hash,
                            result_payload=plan.result_payload,
                        )
                    )
                for impacted_document_id in sorted(impacted_documents, key=str):
                    self._enqueue_graph_event(
                        session,
                        impacted_document_id,
                        "replace",
                    )

    async def set_document_state(
        self,
        document_id: str,
        status: str,
        *,
        error_msg: str | None = None,
        stage_results: dict | None = None,
        source_context: dict | None = None,
        expected_revision: int | None = None,
    ) -> None:
        async with self._session_factory() as session:
            await self._check_retention_lease(session, document_id)
            if expected_revision is not None:
                from .types import RetentionRevisionConflict

                uid = uuid.UUID(document_id)
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(uid)))
                )
                current = int(
                    await session.scalar(
                        select(HindsightDocumentState.revision).where(
                            HindsightDocumentState.document_id == uid
                        )
                    )
                    or 0
                )
                if current != expected_revision:
                    raise RetentionRevisionConflict("retention revision conflict")
            document = await session.get(Document, uuid.UUID(document_id))
            if document is None or not self.scope.permits(
                getattr(document, "bank_id", None) or "default-team",
                getattr(document, "tags", None) or [],
            ):
                raise ValueError(f"document does not exist: {document_id}")
            await session.execute(
                insert(HindsightDocumentState)
                .values(
                    document_id=uuid.UUID(document_id),
                    operation_id=uuid.UUID(document_id),
                    stage_results=stage_results or {"retain": status},
                    source_context=source_context or {},
                    status=status,
                    bank_id=self.scope.bank_id,
                    error_msg=error_msg,
                )
                .on_conflict_do_update(
                    index_elements=[HindsightDocumentState.document_id],
                    set_={
                        "stage_results": HindsightDocumentState.stage_results.op("||")(
                            stage_results or {"retain": status}
                        ),
                        "status": status,
                        "error_msg": error_msg,
                        "updated_at": func.now(),
                        **(
                            {"source_context": source_context}
                            if source_context is not None
                            else {}
                        ),
                    },
                )
            )
            await session.commit()

    async def document_state(self, document_id: str) -> DocumentMemoryState | None:
        return (await self.document_states([document_id])).get(document_id)

    async def document_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]:
        if not document_ids:
            return {}
        ids = [uuid.UUID(document_id) for document_id in document_ids]
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(HindsightDocumentState)
                    .join(Document, Document.id == HindsightDocumentState.document_id)
                    .where(
                        HindsightDocumentState.document_id.in_(ids),
                        self._document_scope(),
                    )
                )
            )
        return {str(row.document_id): self._state_from_row(row) for row in rows}

    async def delete_document(self, document_id: str) -> None:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            async with session.begin():
                document = await session.get(Document, uid)
                if document is None or not self.scope.permits(
                    getattr(document, "bank_id", None) or "default-team",
                    getattr(document, "tags", None) or [],
                ):
                    return
                await session.execute(
                    select(func.pg_advisory_xact_lock(document_lock_key(uid)))
                )
                self._enqueue_graph_event(session, uid, "delete")
                memory_ids = list(
                    await session.scalars(
                        select(MemoryUnit.id).where(
                            MemoryUnit.document_id == uid, self._memory_scope()
                        )
                    )
                )
                impacted_documents = await self._dependent_graph_documents(
                    session,
                    memory_ids,
                    exclude_document_id=uid,
                )
                if memory_ids:
                    rows = list(
                        await session.scalars(
                            select(MemoryUnit).where(MemoryUnit.id.in_(memory_ids))
                        )
                    )
                    await self._invalidate_observation_evidence(
                        session, memory_ids, reason="source_deleted"
                    )
                    await session.execute(
                        delete(MemoryUnit).where(
                            MemoryUnit.memory_type == "observation",
                            self._memory_scope(),
                            MemoryUnit.source_memory_ids.overlap(memory_ids),
                            ~select(ObservationRecord.memory_id)
                            .where(ObservationRecord.memory_id == MemoryUnit.id)
                            .exists(),
                        )
                    )
                    await self._reanchor_observations(session, uid)
                    revision = int(
                        await session.scalar(
                            select(HindsightDocumentState.revision).where(
                                HindsightDocumentState.document_id == uid
                            )
                        )
                        or 1
                    )
                    await self._enqueue_consolidation_changes(
                        session,
                        document_id=uid,
                        document_revision=revision + 1,
                        scope_tags=tuple(getattr(document, "tags", None) or ()),
                        added=[],
                        removed=[
                            row
                            for row in rows
                            if not row.is_source_chunk
                            and row.memory_type in {"world", "experience"}
                        ],
                    )
                await session.execute(
                    delete(MemoryUnit).where(
                        MemoryUnit.document_id == uid,
                        self._memory_scope(),
                        ~select(ObservationRecord.memory_id)
                        .where(ObservationRecord.memory_id == MemoryUnit.id)
                        .exists(),
                    )
                )
                for impacted_document_id in sorted(impacted_documents, key=str):
                    self._enqueue_graph_event(
                        session,
                        impacted_document_id,
                        "replace",
                    )
                await session.execute(
                    delete(HindsightDocumentState).where(
                        HindsightDocumentState.document_id == uid
                    )
                )
                await session.execute(
                    delete(MemoryEntity).where(
                        MemoryEntity.bank_id == self.scope.bank_id,
                        ~select(MemoryUnitEntity.entity_id)
                        .where(MemoryUnitEntity.entity_id == MemoryEntity.id)
                        .exists(),
                    )
                )

    async def purge_orphaned_observations(self) -> int:
        """Physically remove stale observations after all evidence is deleted."""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(ObservationEvidence).where(
                    ObservationEvidence.bank_id == self.scope.bank_id,
                    ObservationEvidence.active.is_(False),
                    ~select(MemoryUnit.id)
                    .where(MemoryUnit.id == ObservationEvidence.fact_id)
                    .exists(),
                )
            )
            orphaned = list(
                await session.scalars(
                    select(ObservationRecord.memory_id)
                    .join(MemoryUnit, MemoryUnit.id == ObservationRecord.memory_id)
                    .where(
                        self._memory_scope(),
                        ObservationRecord.freshness == "stale",
                        ~select(ObservationEvidence.observation_id)
                        .where(
                            ObservationEvidence.observation_id
                            == ObservationRecord.memory_id,
                            ObservationEvidence.active.is_(True),
                        )
                        .exists(),
                    )
                )
            )
            if not orphaned:
                return 0
            await session.execute(
                delete(ObservationHistory).where(
                    ObservationHistory.bank_id == self.scope.bank_id,
                    ObservationHistory.observation_id.in_(orphaned),
                )
            )
            result = await session.execute(
                delete(MemoryUnit).where(
                    MemoryUnit.id.in_(orphaned),
                    self._memory_scope(),
                )
            )
            return int(result.rowcount or 0)

    async def list_backfill_candidates(
        self,
        *,
        document_id: str | None = None,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """List indexed documents eligible for Hindsight backfill, with raw_text."""
        statement = (
            select(Document, HindsightDocumentState.status)
            .outerjoin(
                HindsightDocumentState,
                HindsightDocumentState.document_id == Document.id,
            )
            .where(
                Document.status == "indexed",
                Document.raw_text != "",
                self._document_scope(),
            )
            .order_by(Document.created_at, Document.id)
        )
        if document_id is not None:
            statement = statement.where(Document.id == uuid.UUID(document_id))
        if not force:
            statement = statement.where(
                or_(
                    HindsightDocumentState.document_id.is_(None),
                    HindsightDocumentState.status != "indexed",
                )
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            {
                "document_id": str(document.id),
                "title": document.title,
                "content": document.raw_text,
                "file_type": document.file_type,
                "hindsight_status": hindsight_status,
            }
            for document, hindsight_status in rows
        ]

    async def graph_projection(self, document_id: str) -> MemoryGraphProjection | None:
        uid = uuid.UUID(document_id)
        async with self._session_factory() as session:
            document = await session.get(Document, uid)
            if document is None or not self.scope.permits(
                getattr(document, "bank_id", None) or "default-team",
                getattr(document, "tags", None) or [],
            ):
                return None
            memories = list(
                await session.scalars(
                    select(MemoryUnit)
                    .where(
                        MemoryUnit.document_id == uid,
                        self._memory_scope(),
                        MemoryUnit.state == "active",
                    )
                    .order_by(
                        MemoryUnit.chunk_index,
                        MemoryUnit.memory_index,
                        MemoryUnit.id,
                    )
                )
            )
            memory_ids = [memory.id for memory in memories]
            mention_rows = []
            links = []
            if memory_ids:
                mention_rows = list(
                    (
                        await session.execute(
                            select(MemoryUnitEntity, MemoryEntity)
                            .join(
                                MemoryEntity,
                                MemoryEntity.id == MemoryUnitEntity.entity_id,
                            )
                            .where(MemoryUnitEntity.memory_id.in_(memory_ids))
                        )
                    ).all()
                )
                links = list(
                    await session.scalars(
                        select(MemoryLink).where(
                            MemoryLink.source_memory_id.in_(memory_ids),
                            MemoryLink.target_memory_id.in_(
                                select(MemoryUnit.id).where(
                                    self._memory_scope(), MemoryUnit.state == "active"
                                )
                            ),
                        )
                    )
                )
        return self._graph_projection(document, memories, mention_rows, links)

    async def _insert_memories(
        self, session: AsyncSession, plan: RetainPlan, *, scope_tags=(), retained_ids=()
    ) -> None:
        for draft in plan.memories:
            row = MemoryUnit(
                bank_id=self.scope.bank_id,
                id=uuid.UUID(draft.id),
                document_id=uuid.UUID(draft.document_id),
                chunk_index=draft.chunk_index,
                memory_index=draft.memory_index,
                memory_type=draft.memory_type,
                text=draft.text,
                lexical_tokens=lexical_tokens(draft.text),
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
                scope_tags=list(scope_tags),
                memory_version=max(1, plan.revision),
                metadata_json={
                    **draft.metadata,
                    "entity_mentions": list(draft.entities),
                },
            )
            if row.id in retained_ids:
                existing = await session.get(MemoryUnit, row.id)
                preserved = dict(existing.metadata_json or {})
                # A stable fact keeps its manually corrected ownership and source
                # timestamp. Updating its position must not cascade external links.
                corrected = "entity_correction_id" in preserved
                if corrected:
                    for key in (
                        "entity_correction_id",
                        "resolved_entities",
                        "entity_mentions",
                    ):
                        if key in preserved:
                            row.metadata_json[key] = preserved[key]
                for attribute in (
                    "chunk_index",
                    "memory_index",
                    "context",
                    "embedding",
                    "confidence",
                    "tags",
                    "scope_tags",
                    "metadata_json",
                ):
                    setattr(existing, attribute, getattr(row, attribute))
                if corrected:
                    continue
                await session.execute(
                    delete(MemoryUnitEntity).where(
                        MemoryUnitEntity.memory_id == row.id,
                        MemoryUnitEntity.bank_id == self.scope.bank_id,
                    )
                )
            else:
                session.add(row)
            for entity_name in draft.entities:
                normalized = normalize_entity(entity_name)
                if not normalized:
                    continue
                resolved = next(
                    (
                        item
                        for item in draft.metadata.get("resolved_entities", [])
                        if item["name"] == entity_name
                    ),
                    None,
                )
                if resolved is not None:
                    entity_id = uuid.UUID(resolved["id"])
                    if not resolved["existing"]:
                        await session.execute(
                            insert(MemoryEntity)
                            .values(
                                id=entity_id,
                                canonical_name=entity_name.strip(),
                                normalized_name=normalized,
                                identity_key=str(entity_id),
                                bank_id=self.scope.bank_id,
                            )
                            .on_conflict_do_nothing(index_elements=[MemoryEntity.id])
                        )
                        owner = await session.get(MemoryEntity, entity_id)
                        if (
                            owner is None
                            or owner.bank_id != self.scope.bank_id
                            or owner.identity_key != str(entity_id)
                        ):
                            raise ValueError("entity identity conflict")
                    await session.execute(
                        insert(MemoryUnitEntity)
                        .values(
                            memory_id=row.id,
                            entity_id=entity_id,
                            bank_id=self.scope.bank_id,
                            original_name=entity_name,
                            aliases=list(
                                draft.metadata.get("entity_aliases", {}).get(
                                    normalized, []
                                )
                            ),
                        )
                        .on_conflict_do_nothing()
                    )
                    continue
                await session.execute(
                    insert(MemoryEntity)
                    .values(
                        canonical_name=entity_name.strip(),
                        normalized_name=normalized,
                        identity_key="",
                        bank_id=self.scope.bank_id,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            MemoryEntity.bank_id,
                            MemoryEntity.normalized_name,
                            MemoryEntity.identity_key,
                        ]
                    )
                )
                entity_id = await session.scalar(
                    select(MemoryEntity.id).where(
                        MemoryEntity.normalized_name == normalized,
                        MemoryEntity.identity_key == "",
                        MemoryEntity.bank_id == self.scope.bank_id,
                    )
                )
                if entity_id is None:
                    raise RuntimeError(f"failed to persist entity: {entity_name}")
                await session.execute(
                    insert(MemoryUnitEntity)
                    .values(
                        memory_id=row.id,
                        entity_id=entity_id,
                        bank_id=self.scope.bank_id,
                        original_name=entity_name,
                        aliases=list(
                            draft.metadata.get("entity_aliases", {}).get(normalized, [])
                        ),
                    )
                    .on_conflict_do_nothing()
                )
        await session.flush()

    async def _enqueue_consolidation_changes(
        self,
        session: AsyncSession,
        *,
        document_id: uuid.UUID,
        document_revision: int,
        scope_tags: tuple[str, ...],
        added: list,
        removed: list[MemoryUnit],
    ) -> None:
        """Write fact events and one coalesced job in the caller's transaction."""
        import json

        if not self._consolidation_enabled:
            return

        jobs: dict[str, tuple[str, ...]] = {}
        for row in removed:
            session.add(
                FactTombstone(
                    bank_id=self.scope.bank_id,
                    fact_id=row.id,
                    fact_version=row.memory_version,
                    document_id=document_id,
                    document_revision=document_revision,
                )
            )
        for configured_scope in self.scope.observation_scopes or ((),):
            write_scope = tuple(sorted(set(configured_scope)))
            if not set(write_scope).issubset(scope_tags):
                continue
            scope_key = json.dumps(
                write_scope, ensure_ascii=False, separators=(",", ":")
            )
            jobs[scope_key] = write_scope
            for draft in added:
                session.add(
                    ConsolidationFactEvent(
                        bank_id=self.scope.bank_id,
                        scope_key=scope_key,
                        write_scope=list(write_scope),
                        fact_id=uuid.UUID(draft.id),
                        fact_version=max(1, document_revision),
                        document_id=document_id,
                        document_revision=document_revision,
                        operation="upsert",
                    )
                )
            for row in removed:
                session.add(
                    ConsolidationFactEvent(
                        bank_id=self.scope.bank_id,
                        scope_key=scope_key,
                        write_scope=list(write_scope),
                        fact_id=row.id,
                        fact_version=row.memory_version,
                        document_id=document_id,
                        document_revision=document_revision,
                        operation="delete",
                    )
                )
        if removed:
            await self._invalidate_mental_model_sources(
                session, [row.id for row in removed]
            )
        if not jobs or not (added or removed):
            return
        await session.flush()
        operation_id = await session.scalar(
            select(HindsightDocumentState.operation_id).where(
                HindsightDocumentState.document_id == document_id,
                HindsightDocumentState.bank_id == self.scope.bank_id,
            )
        )
        for scope_key, write_scope in jobs.items():
            pending_through = await session.scalar(
                select(func.max(ConsolidationFactEvent.id)).where(
                    ConsolidationFactEvent.bank_id == self.scope.bank_id,
                    ConsolidationFactEvent.scope_key == scope_key,
                )
            )

            if pending_through is None:
                continue
            active_lease = (
                (ConsolidationJob.status == "processing")
                & ConsolidationJob.lease_expires_at.is_not(None)
                & (ConsolidationJob.lease_expires_at > func.clock_timestamp())
            )
            await session.execute(
                insert(ConsolidationJob)
                .values(
                    bank_id=self.scope.bank_id,
                    scope_key=scope_key,
                    write_scope=list(write_scope),
                    status="pending",
                    pending_through=pending_through,
                    processed_through=0,
                    available_at=func.now(),
                    operation_id=operation_id or uuid.uuid4(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        ConsolidationJob.bank_id,
                        ConsolidationJob.scope_key,
                    ],
                    set_={
                        "pending_through": func.greatest(
                            ConsolidationJob.pending_through, pending_through
                        ),
                        "status": case(
                            (active_lease, ConsolidationJob.status), else_="pending"
                        ),
                        "attempts": case(
                            (active_lease, ConsolidationJob.attempts), else_=0
                        ),
                        "iterations": case(
                            (active_lease, ConsolidationJob.iterations), else_=0
                        ),
                        "tokens_used": case(
                            (active_lease, ConsolidationJob.tokens_used), else_=0
                        ),
                        "cost_microusd": case(
                            (active_lease, ConsolidationJob.cost_microusd), else_=0
                        ),
                        "available_at": case(
                            (active_lease, ConsolidationJob.available_at),
                            else_=func.now(),
                        ),
                        "error_msg": case(
                            (active_lease, ConsolidationJob.error_msg), else_=None
                        ),
                        "lease_token": case(
                            (active_lease, ConsolidationJob.lease_token), else_=None
                        ),
                        "lease_expires_at": case(
                            (active_lease, ConsolidationJob.lease_expires_at),
                            else_=None,
                        ),
                        "updated_at": func.now(),
                        "operation_id": case(
                            (active_lease, ConsolidationJob.operation_id),
                            else_=operation_id or ConsolidationJob.operation_id,
                        ),
                    },
                )
            )

    async def _invalidate_mental_model_sources(
        self, session: AsyncSession, fact_ids: list[uuid.UUID]
    ) -> None:
        models = list(
            await session.scalars(
                select(MentalModelRow).where(
                    MentalModelRow.bank_id == self.scope.bank_id,
                    MentalModelRow.source_memory_ids.overlap(fact_ids),
                )
            )
        )
        for model in models:
            model.freshness = "stale"
            model.error_msg = "source_deleted"
            requested = model.evidence_watermark + 1
            statement = insert(MentalModelRefreshJob).values(
                bank_id=model.bank_id,
                model_id=model.id,
                requested_watermark=requested,
                status="pending",
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=["bank_id", "model_id"],
                    set_={
                        "requested_watermark": func.greatest(
                            MentalModelRefreshJob.requested_watermark, requested
                        ),
                        "status": "pending",
                        "available_at": func.now(),
                        "lease_token": None,
                        "lease_expires_at": None,
                        "error_msg": "source_deleted",
                        "updated_at": func.now(),
                    },
                )
            )

    async def _invalidate_observation_evidence(
        self, session: AsyncSession, fact_ids: list[uuid.UUID], *, reason: str
    ) -> None:
        if not fact_ids:
            return
        observation_ids = list(
            await session.scalars(
                select(ObservationEvidence.observation_id)
                .where(
                    ObservationEvidence.bank_id == self.scope.bank_id,
                    ObservationEvidence.fact_id.in_(fact_ids),
                    ObservationEvidence.active.is_(True),
                )
                .distinct()
            )
        )
        if not observation_ids:
            return
        await session.execute(
            ObservationEvidence.__table__.update()
            .where(
                ObservationEvidence.bank_id == self.scope.bank_id,
                ObservationEvidence.fact_id.in_(fact_ids),
            )
            .values(active=False)
        )
        await session.execute(
            ObservationRecord.__table__.update()
            .where(ObservationRecord.memory_id.in_(observation_ids))
            .values(freshness="stale", stale_reason=reason, updated_at=func.now())
        )
        await session.execute(
            MemoryUnit.__table__.update()
            .where(MemoryUnit.id.in_(observation_ids))
            .values(state="stale")
        )

    async def _reanchor_observations(
        self, session: AsyncSession, deleted_document_id: uuid.UUID
    ) -> None:
        """Keep cross-source observations available for safe recomputation."""
        candidates = list(
            await session.scalars(
                select(MemoryUnit.id).where(
                    MemoryUnit.document_id == deleted_document_id,
                    MemoryUnit.memory_type == "observation",
                    MemoryUnit.id.in_(select(ObservationRecord.memory_id)),
                )
            )
        )
        for observation_id in candidates:
            replacement = await session.scalar(
                select(MemoryUnit.document_id)
                .join(ObservationEvidence, ObservationEvidence.fact_id == MemoryUnit.id)
                .where(
                    ObservationEvidence.observation_id == observation_id,
                    ObservationEvidence.active.is_(True),
                    MemoryUnit.state == "active",
                    MemoryUnit.document_id != deleted_document_id,
                )
                .order_by(MemoryUnit.id)
                .limit(1)
            )
            if replacement is not None:
                await session.execute(
                    MemoryUnit.__table__.update()
                    .where(MemoryUnit.id == observation_id)
                    .values(document_id=replacement)
                )

    async def _dependent_graph_documents(
        self,
        session: AsyncSession,
        memory_ids: list[uuid.UUID],
        *,
        exclude_document_id: uuid.UUID,
    ) -> set[uuid.UUID]:
        if not memory_ids:
            return set()
        observation_documents = set(
            await session.scalars(
                select(MemoryUnit.document_id)
                .where(
                    MemoryUnit.document_id != exclude_document_id,
                    MemoryUnit.memory_type == "observation",
                    self._memory_scope(),
                    MemoryUnit.source_memory_ids.overlap(memory_ids),
                )
                .distinct()
            )
        )
        inbound_link_documents = set(
            await session.scalars(
                select(MemoryUnit.document_id)
                .join(
                    MemoryLink,
                    MemoryLink.source_memory_id == MemoryUnit.id,
                )
                .where(
                    MemoryUnit.document_id != exclude_document_id,
                    MemoryLink.target_memory_id.in_(memory_ids),
                    self._memory_scope(),
                )
                .distinct()
            )
        )
        return observation_documents | inbound_link_documents

    @staticmethod
    def _state_from_row(row: HindsightDocumentState) -> DocumentMemoryState:
        return DocumentMemoryState(
            document_id=str(row.document_id),
            status=row.status,
            error_msg=row.error_msg,
            memory_count=row.memory_count,
            link_count=row.link_count,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )

    def _enqueue_graph_event(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
        operation: str,
    ) -> HindsightGraphOutbox:
        if operation not in {"replace", "delete"}:
            raise ValueError(f"unsupported graph operation: {operation}")
        event = HindsightGraphOutbox(
            bank_id=self.scope.bank_id,
            document_id=document_id,
            operation=operation,
        )
        session.add(event)
        return event

    @staticmethod
    def _graph_projection(
        document: Document,
        memories: list[MemoryUnit],
        mention_rows: list[tuple[MemoryUnitEntity, MemoryEntity]],
        links: list[MemoryLink],
    ) -> MemoryGraphProjection:
        entity_rows = {entity.id: entity for _, entity in mention_rows}
        return MemoryGraphProjection(
            document=MemoryGraphDocument(
                bank_id=getattr(document, "bank_id", None) or "default-team",
                tags=tuple(getattr(document, "tags", None) or []),
                id=str(document.id),
                title=document.title,
                file_type=document.file_type,
                overview=document.overview or "",
            ),
            memories=tuple(
                MemoryGraphMemory(
                    id=str(memory.id),
                    document_id=str(memory.document_id),
                    memory_type=memory.memory_type,
                    text=memory.text,
                    context=memory.context,
                    chunk_index=memory.chunk_index,
                    occurred_start=(
                        memory.occurred_start.isoformat()
                        if memory.occurred_start
                        else None
                    ),
                    occurred_end=(
                        memory.occurred_end.isoformat() if memory.occurred_end else None
                    ),
                    confidence=memory.confidence,
                    source_memory_ids=tuple(
                        str(item) for item in memory.source_memory_ids
                    ),
                    tags=tuple(memory.tags),
                    metadata=dict(memory.metadata_json or {}),
                )
                for memory in memories
            ),
            entities=tuple(
                MemoryGraphEntity(
                    id=str(entity.id),
                    canonical_name=entity.canonical_name,
                    normalized_name=entity.normalized_name,
                    entity_type=entity.entity_type,
                    metadata=dict(entity.metadata_json or {}),
                )
                for entity in sorted(
                    entity_rows.values(), key=lambda item: item.normalized_name
                )
            ),
            mentions=tuple(
                MemoryGraphMention(
                    memory_id=str(mention.memory_id),
                    entity_id=str(mention.entity_id),
                    role=mention.role,
                )
                for mention, _ in mention_rows
            ),
            links=tuple(
                MemoryGraphLinkProjection(
                    source_memory_id=str(link.source_memory_id),
                    target_memory_id=str(link.target_memory_id),
                    link_type=link.link_type,
                    weight=link.weight,
                    metadata=dict(link.metadata_json or {}),
                )
                for link in links
            ),
        )

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
                    select(MemoryUnit.id).where(
                        MemoryUnit.id.in_(external_ids), self._memory_scope()
                    )
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
            if link.link_type == "entity":
                shared = await session.scalar(
                    select(MemoryUnitEntity.entity_id)
                    .where(
                        MemoryUnitEntity.memory_id == source_id,
                        MemoryUnitEntity.entity_id.in_(
                            select(MemoryUnitEntity.entity_id)
                            .where(MemoryUnitEntity.memory_id == target_id)
                            .correlate(None)
                        ),
                    )
                    .limit(1)
                )
                if shared is None:
                    continue
            await session.execute(
                insert(MemoryLink)
                .values(
                    bank_id=self.scope.bank_id,
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
                    self._memory_scope(),
                    MemoryUnit.state == "active",
                    MemoryUnit.embedding.is_not(None),
                )
                .order_by(score.desc())
                .limit(limit)
            )
        return [(str(memory_id), float(value)) for memory_id, value in rows]

    async def semantic_search(
        self,
        embedding: list[float],
        limit: int,
        *,
        source_type: str | None = None,
        filters: RecallFilter | None = None,
    ) -> list[RecallCandidate]:
        score = (1 - MemoryUnit.embedding.cosine_distance(embedding)).label("score")
        async with self._session_factory() as session:
            rows = await session.execute(
                select(MemoryUnit, Document, score)
                .join(Document, Document.id == MemoryUnit.document_id)
                .where(
                    self._memory_scope(),
                    MemoryUnit.embedding.is_not(None),
                    Document.status == "indexed",
                    *self._recall_source_conditions(source_type, filters),
                )
                .order_by(score.desc())
                .limit(limit)
            )
        candidates = [
            self._candidate(unit, document, semantic_score=float(value))
            for unit, document, value in rows
        ]
        from .file_chunk_recall import search_file_chunks

        candidates.extend(
            await search_file_chunks(
                self._session_factory,
                self.scope,
                embedding,
                limit,
                source_type,
                filters,
            )
        )
        return sorted(
            candidates, key=lambda item: item.semantic_score or 0, reverse=True
        )[:limit]

    async def keyword_search(
        self,
        query: str,
        limit: int,
        *,
        source_type: str | None = None,
        filters: RecallFilter | None = None,
    ) -> list[RecallCandidate]:
        query_tokens = list(dict.fromkeys(lexical_tokens(query)))
        if not query_tokens:
            return []
        async with self._session_factory() as session:
            conditions = (
                self._memory_scope(),
                Document.status == "indexed",
                *self._recall_source_conditions(source_type, filters),
            )
            base = (
                select(MemoryUnit.id, MemoryUnit.text)
                .join(Document, Document.id == MemoryUnit.document_id)
                .where(*conditions)
            )
            params: dict[str, Any] = {}
            if self._keyword_index_enabled:
                token = func.unnest(MemoryUnit.lexical_tokens).column_valued("token")
                overlap_score = (
                    select(func.count(func.distinct(token)))
                    .where(
                        token
                        == func.any(
                            bindparam("keyword_query_tokens", type_=ARRAY(Text))
                        )
                    )
                    .correlate(MemoryUnit)
                    .scalar_subquery()
                )
                base = (
                    base.add_columns(overlap_score.label("lexical_overlap"))
                    .where(
                        MemoryUnit.lexical_tokens.is_not(None),
                        MemoryUnit.lexical_tokens.overlap(query_tokens),
                    )
                    .order_by(overlap_score.desc(), MemoryUnit.id)
                    .limit(self._keyword_candidate_limit)
                )
                params["keyword_query_tokens"] = query_tokens
            result = (
                await session.execute(base, params)
                if params
                else await session.execute(base)
            )
            raw_rows = list(result.all())
            scores = self._bm25(query, [str(row[1]) for row in raw_rows])
            ranked_ids = sorted(
                (
                    (memory_id, score)
                    for (memory_id, _text, *_), score in zip(
                        raw_rows, scores, strict=True
                    )
                    if score > 0
                ),
                key=lambda item: (-item[1], str(item[0])),
            )[:limit]
            if not ranked_ids:
                return []
            score_by_id = dict(ranked_ids)
            rows = list(
                (
                    await session.execute(
                        select(MemoryUnit, Document)
                        .join(Document, Document.id == MemoryUnit.document_id)
                        .where(
                            MemoryUnit.id.in_(score_by_id),
                            self._memory_scope(),
                            *self._recall_source_conditions(source_type, filters),
                        )
                    )
                ).all()
            )
        candidates = {
            unit.id: self._candidate(unit, document, keyword_score=score_by_id[unit.id])
            for unit, document in rows
        }
        return [candidates[memory_id] for memory_id, _score in ranked_ids]

    async def graph_search(
        self,
        entities: list[str],
        limit: int,
        *,
        source_type: str | None = None,
        filters: RecallFilter | None = None,
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
                    self._memory_scope(),
                    Document.status == "indexed",
                    *self._recall_source_conditions(source_type, filters),
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
                                self._memory_scope(),
                                Document.status == "indexed",
                                *self._recall_source_conditions(source_type, filters),
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
        *,
        source_type: str | None = None,
        filters: RecallFilter | None = None,
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
                    self._memory_scope(),
                    Document.status == "indexed",
                    *self._recall_source_conditions(source_type, filters),
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
                .where(MemoryUnit.id.in_(ids), self._memory_scope())
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

    async def recall_details(
        self, memory_ids: list[str], *, include_source_facts: bool = False
    ) -> dict[str, dict[str, Any]]:
        if not memory_ids:
            return {}
        ids = [uuid.UUID(item) for item in memory_ids]
        async with self._session_factory() as session:
            records = list(
                (
                    await session.execute(
                        select(ObservationRecord, MemoryUnit)
                        .join(MemoryUnit, MemoryUnit.id == ObservationRecord.memory_id)
                        .where(
                            ObservationRecord.memory_id.in_(ids),
                            self._memory_scope(),
                            MemoryUnit.state.in_(("active", "stale")),
                        )
                    )
                ).all()
            )
            facts_by_observation: defaultdict[str, list[dict[str, Any]]] = defaultdict(
                list
            )
            if include_source_facts and records:
                observation_ids = [record.memory_id for record, _ in records]
                facts = await session.execute(
                    select(ObservationEvidence, MemoryUnit)
                    .join(MemoryUnit, MemoryUnit.id == ObservationEvidence.fact_id)
                    .where(
                        ObservationEvidence.observation_id.in_(observation_ids),
                        ObservationEvidence.active.is_(True),
                        MemoryUnit.state == "active",
                        self._memory_scope(),
                    )
                    .order_by(ObservationEvidence.observation_id, MemoryUnit.id)
                )
                for edge, fact in facts:
                    facts_by_observation[str(edge.observation_id)].append(
                        {
                            "id": str(fact.id),
                            "text": fact.text,
                            "type": fact.memory_type,
                            "metadata": {"memory_version": fact.memory_version},
                            "updated_at": fact.mentioned_at.isoformat(),
                            "document_id": str(fact.document_id),
                            "mentioned_at": fact.mentioned_at.isoformat(),
                            "occurred_start": fact.occurred_start.isoformat()
                            if fact.occurred_start
                            else None,
                            "occurred_end": fact.occurred_end.isoformat()
                            if fact.occurred_end
                            else None,
                        }
                    )
        return {
            str(record.memory_id): {
                "freshness": record.freshness,
                "stale_reason": record.stale_reason,
                "updated_at": record.updated_at.isoformat(),
                "source_facts": facts_by_observation[str(record.memory_id)],
            }
            for record, _ in records
        }

    async def load_cached_facts(self, memory_ids: list[str], *, filters=None):
        """Bulk authoritative validation, including current request filters."""
        if not memory_ids:
            return {}
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MemoryUnit, Document)
                    .join(Document, Document.id == MemoryUnit.document_id)
                    .where(
                        MemoryUnit.id.in_([uuid.UUID(i) for i in memory_ids]),
                        self._memory_scope(),
                        Document.status == "indexed",
                        MemoryUnit.state == "active",
                        MemoryUnit.is_source_chunk.is_(False),
                        MemoryUnit.memory_type.in_(("world", "experience")),
                        *self._recall_source_conditions(None, filters),
                    )
                )
            ).all()
        return {
            str(unit.id): self._candidate(unit, document).as_evidence()
            for unit, document in rows
        }

    async def expand_memory_record(self, memory_id: str) -> dict[str, Any] | None:
        try:
            identity = uuid.UUID(memory_id)
        except ValueError:
            return None
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(MemoryUnit, Document)
                    .join(Document, Document.id == MemoryUnit.document_id)
                    .where(
                        MemoryUnit.id == identity,
                        self._memory_scope(),
                        MemoryUnit.state.in_(("active", "stale")),
                        Document.status == "indexed",
                        *self._recall_source_conditions(
                            None, RecallFilter(include_stale=True)
                        ),
                    )
                )
            ).one_or_none()
        if row is None:
            from .file_chunk_recall import expand_file_chunk

            return await expand_file_chunk(self._session_factory, self.scope, identity)
        unit, document = row
        detail = (
            await self.recall_details([memory_id], include_source_facts=True)
        ).get(memory_id, {})
        return {
            "memory": self._candidate(unit, document).as_evidence(),
            "chunk": {
                "id": f"{unit.document_id}_{unit.chunk_index}",
                "document_id": str(unit.document_id),
                "chunk_index": unit.chunk_index,
                "text": unit.source_text,
            },
            "document": {
                "id": str(document.id),
                "title": document.title,
                "file_type": document.file_type,
                "text": document.raw_text,
                "updated_at": document.updated_at.isoformat(),
            },
            "source_facts": detail.get("source_facts", []),
            "freshness": detail.get("freshness", unit.state),
            "stale_reason": detail.get("stale_reason"),
            "updated_at": detail.get("updated_at", unit.mentioned_at.isoformat()),
        }

    async def reflection_context(
        self, query: str, query_embedding: list[float]
    ) -> ReflectionContext:
        async with self._session_factory() as session:
            models = list(
                (
                    await session.scalars(
                        select(MentalModelRow).where(
                            scope_predicate(
                                MentalModelRow.bank_id, MentalModelRow.tags, self.scope
                            )
                        )
                    )
                ).all()
            )
            profile = await session.get(
                MemoryProfileRow, ("default", self.scope.bank_id)
            )
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
                    source_query=row.source_query or row.description,
                    version=row.version,
                    freshness=row.freshness,
                    last_success_at=row.last_success_at,
                    source_versions={
                        str(key): int(value)
                        for key, value in row.source_versions.items()
                    },
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
        metadata = dict(unit.metadata_json or {})
        metadata["memory_version"] = getattr(unit, "memory_version", 1)
        metadata["is_source_chunk"] = bool(getattr(unit, "is_source_chunk", False))
        mentioned_at = getattr(unit, "mentioned_at", None)
        return RecallCandidate(
            id=str(unit.id),
            document_id=str(unit.document_id),
            title=document.title,
            text=unit.text,
            source_text=unit.source_text,
            chunk_index=unit.chunk_index,
            source_type=str(metadata.get("source_type") or "upload"),
            session_id=(
                str(metadata["session_id"]) if metadata.get("session_id") else None
            ),
            turn_id=str(metadata["turn_id"]) if metadata.get("turn_id") else None,
            memory_type=unit.memory_type,
            context=unit.context,
            occurred_start=(
                unit.occurred_start.isoformat() if unit.occurred_start else None
            ),
            occurred_end=unit.occurred_end.isoformat() if unit.occurred_end else None,
            mentioned_at=mentioned_at.isoformat() if mentioned_at else None,
            updated_at=mentioned_at.isoformat() if mentioned_at else None,
            freshness=getattr(unit, "state", "active"),
            metadata=metadata,
            source_memory_ids=[str(item) for item in unit.source_memory_ids],
            embedding=[float(v) for v in unit.embedding]
            if unit.embedding is not None
            else None,
            **scores,
        )

    @staticmethod
    def _recall_source_conditions(
        source_type: str | None, filters: RecallFilter | None = None
    ) -> list[Any]:
        filters = filters or RecallFilter()
        completed_conversation = (
            select(ConversationMemorySource.document_id)
            .where(
                ConversationMemorySource.document_id == Document.id,
                ConversationMemorySource.status == "completed",
            )
            .exists()
        )
        conditions = [or_(Document.file_type != "conversation", completed_conversation)]
        conditions.append(
            MemoryUnit.state.in_(("active", "stale"))
            if filters.include_stale
            else MemoryUnit.state == "active"
        )
        if source_type is not None:
            conditions.append(
                or_(
                    MemoryUnit.metadata_json["source_type"].astext == source_type,
                    MemoryUnit.metadata_json["source_types"].contains([source_type]),
                )
            )
        if filters.source_types:
            conditions.append(
                or_(
                    MemoryUnit.metadata_json["source_type"].astext.in_(
                        filters.source_types
                    ),
                    *[
                        MemoryUnit.metadata_json["source_types"].contains([item])
                        for item in filters.source_types
                    ],
                )
            )
        if filters.memory_types:
            conditions.append(MemoryUnit.memory_type.in_(filters.memory_types))
        if filters.tags is not None:
            conditions.append(tag_predicate(MemoryUnit.tags, filters.tags))
        if filters.reference_time is not None:
            conditions.append(MemoryUnit.mentioned_at <= filters.reference_time)
        return conditions

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
