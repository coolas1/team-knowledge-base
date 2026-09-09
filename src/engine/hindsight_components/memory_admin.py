"""Scoped diagnostic and memory-management queries."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import and_, func, select

from src.engine.components.store.models import Document
from src.engine.components.store.scope import scope_predicate
from src.engine.scope import MemoryScope

from .models import (
    ConsolidationJob,
    ConversationMemorySource,
    HindsightDocumentState,
    MemoryUnit,
    MentalModel,
    MentalModelRefreshJob,
    ObservationEvidence,
    ObservationHistory,
    ObservationRecord,
)


@dataclass(slots=True)
class OperationView:
    id: str
    status: str
    stages: dict[str, str] = field(default_factory=dict)
    kind: str = "operation"
    subject: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    document_id: str | None = None
    model_id: str | None = None
    attempts: int = 0
    error: str | None = None
    duration_ms: int | None = None
    tokens: int = 0
    cost_microusd: int = 0


class PostgresMemoryAdminRepository:
    def __init__(self, session_factory=None, *, scope: MemoryScope | None = None):
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory
        self.scope = scope or MemoryScope()

    def with_scope(self, scope: MemoryScope):
        return PostgresMemoryAdminRepository(self._session_factory, scope=scope)

    def _document_scope(self):
        return scope_predicate(Document.bank_id, Document.tags, self.scope)

    async def list_operations(
        self,
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        limit: int = 100,
    ) -> list[OperationView]:
        if not 1 <= limit <= 500:
            raise ValueError("operation limit must be between 1 and 500")
        async with self._session_factory() as session:
            conversation_conditions = [self._document_scope()]
            if session_id:
                conversation_conditions.append(
                    ConversationMemorySource.session_id == session_id
                )
            if turn_id:
                conversation_conditions.append(
                    ConversationMemorySource.turn_id == turn_id
                )
            conversations = list(
                (
                    await session.execute(
                        select(ConversationMemorySource, Document)
                        .join(
                            Document,
                            Document.id == ConversationMemorySource.document_id,
                        )
                        .where(*conversation_conditions)
                        .order_by(ConversationMemorySource.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            states = list(
                (
                    await session.execute(
                        select(HindsightDocumentState, Document)
                        .join(
                            Document, Document.id == HindsightDocumentState.document_id
                        )
                        .where(self._document_scope())
                        .order_by(HindsightDocumentState.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
            consolidation = list(
                await session.scalars(
                    select(ConsolidationJob)
                    .where(
                        scope_predicate(
                            ConsolidationJob.bank_id,
                            ConsolidationJob.write_scope,
                            self.scope,
                        )
                    )
                    .order_by(ConsolidationJob.updated_at.desc())
                    .limit(limit)
                )
            )
            refreshes = list(
                (
                    await session.execute(
                        select(MentalModelRefreshJob, MentalModel)
                        .join(
                            MentalModel,
                            and_(
                                MentalModel.bank_id == MentalModelRefreshJob.bank_id,
                                MentalModel.id == MentalModelRefreshJob.model_id,
                            ),
                        )
                        .where(
                            scope_predicate(
                                MentalModel.bank_id, MentalModel.tags, self.scope
                            )
                        )
                        .order_by(MentalModelRefreshJob.updated_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        output: dict[str, OperationView] = {}
        for source, document in conversations:
            identity = str(source.operation_id)
            output[identity] = OperationView(
                id=identity,
                status=source.status,
                stages=dict(source.stage_results or {}),
                kind="conversation",
                subject=document.title,
                session_id=source.session_id,
                turn_id=source.turn_id,
                document_id=str(source.document_id),
                attempts=source.attempts,
                error=source.error_msg,
                duration_ms=self._duration(source.created_at, source.updated_at),
            )
        for state, document in states:
            # Old terminal document states predate operation stage tracking.
            # They are document history, not actionable diagnostic tasks.
            if state.status == "indexed" and not state.stage_results:
                continue
            item = output.setdefault(
                str(state.operation_id),
                OperationView(
                    id=str(state.operation_id),
                    status=state.status,
                    stages={"retain": state.status},
                    kind="document",
                    subject=document.title,
                    document_id=str(state.document_id),
                ),
            )
            item.stages.update(dict(state.stage_results or {}))
            item.status = self._merge_status(item.status, state.status)
            item.error = item.error or state.error_msg
        for job in consolidation:
            diagnostic_status = (
                "cancelled"
                if job.error_msg == "cancelled_by_admin"
                else job.status
            )
            subject = (
                "默认归纳范围"
                if job.scope_key == "[]"
                else f"归纳范围 {', '.join(job.write_scope)}"
            )
            item = output.setdefault(
                str(job.operation_id),
                OperationView(
                    id=str(job.operation_id),
                    status=diagnostic_status,
                    kind="consolidation",
                    subject=subject,
                ),
            )
            item.stages["consolidation"] = diagnostic_status
            item.status = self._merge_status(item.status, diagnostic_status)
            item.attempts = max(item.attempts, job.attempts)
            item.error = item.error or job.error_msg
            item.tokens += job.tokens_used
            item.cost_microusd += job.cost_microusd
            item.duration_ms = self._duration(job.created_at, job.updated_at)
        for job, model in refreshes:
            item = output.setdefault(
                str(job.operation_id),
                OperationView(
                    id=str(job.operation_id),
                    status=job.status,
                    kind="mental_model",
                    subject=model.name,
                    model_id=job.model_id,
                ),
            )
            item.stages["mental_model_refresh"] = job.status
            item.status = self._merge_status(item.status, job.status)
            item.attempts = max(item.attempts, job.attempts)
            item.error = item.error or job.error_msg
        values = list(output.values())
        if session_id or turn_id:
            values = [
                item
                for item in values
                if (not session_id or item.session_id == session_id)
                and (not turn_id or item.turn_id == turn_id)
            ]
        return values[:limit]

    async def get_operation(self, operation_id: str) -> OperationView | None:
        try:
            identity = str(uuid.UUID(operation_id))
        except ValueError:
            return None
        return next(
            (
                item
                for item in await self.list_operations(limit=500)
                if item.id == identity
            ),
            None,
        )

    async def retry_operation(self, operation_id: str) -> int:
        return await self._change_operation(operation_id, retry=True)

    async def cancel_operation(self, operation_id: str) -> int:
        return await self._change_operation(operation_id, retry=False)

    async def _change_operation(self, operation_id: str, *, retry: bool) -> int:
        visible = await self.get_operation(operation_id)
        if visible is None:
            raise KeyError(operation_id)
        identity = uuid.UUID(operation_id)
        changed = 0
        async with self._session_factory() as session, session.begin():
            source = await session.scalar(
                select(ConversationMemorySource)
                .join(Document, Document.id == ConversationMemorySource.document_id)
                .where(
                    ConversationMemorySource.operation_id == identity,
                    self._document_scope(),
                )
                .with_for_update()
            )
            if source is not None and source.status != "completed":
                source.status = "pending" if retry else "cancelled"
                source.attempts = 0 if retry else source.attempts
                source.error_msg = None if retry else "cancelled_by_admin"
                source.lease_token = None
                source.lease_expires_at = None
                source.available_at = func.now()
                changed += 1
            state = await session.scalar(
                select(HindsightDocumentState)
                .join(Document, Document.id == HindsightDocumentState.document_id)
                .where(
                    HindsightDocumentState.operation_id == identity,
                    self._document_scope(),
                    HindsightDocumentState.status.in_(
                        ("failed", "degraded", "pending", "processing")
                    ),
                )
                .with_for_update()
            )
            if state is not None:
                state.status = "pending" if retry else "failed"
                state.error_msg = None if retry else "cancelled_by_admin"
                changed += 1
            consolidations = list(
                await session.scalars(
                    select(ConsolidationJob)
                    .where(
                        ConsolidationJob.operation_id == identity,
                        scope_predicate(
                            ConsolidationJob.bank_id,
                            ConsolidationJob.write_scope,
                            self.scope,
                        ),
                        ConsolidationJob.status != "completed",
                    )
                    .with_for_update()
                )
            )
            for job in consolidations:
                job.status = "pending" if retry else "budget_exhausted"
                job.attempts = 0 if retry else job.attempts
                if retry:
                    job.iterations = 0
                    job.tokens_used = 0
                    job.cost_microusd = 0
                job.error_msg = None if retry else "cancelled_by_admin"
                job.lease_token = None
                job.lease_expires_at = None
                job.available_at = func.now()
                changed += 1
            refreshes = list(
                await session.scalars(
                    select(MentalModelRefreshJob)
                    .join(
                        MentalModel,
                        and_(
                            MentalModel.bank_id == MentalModelRefreshJob.bank_id,
                            MentalModel.id == MentalModelRefreshJob.model_id,
                        ),
                    )
                    .where(
                        MentalModelRefreshJob.operation_id == identity,
                        scope_predicate(
                            MentalModel.bank_id, MentalModel.tags, self.scope
                        ),
                        MentalModelRefreshJob.status != "completed",
                    )
                    .with_for_update()
                )
            )
            for job in refreshes:
                job.status = "pending" if retry else "failed"
                job.attempts = 0 if retry else job.attempts
                job.error_msg = None if retry else "cancelled_by_admin"
                job.lease_token = None
                job.lease_expires_at = None
                job.available_at = func.now()
                changed += 1
        return changed

    async def list_facts(self, *, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 500:
            raise ValueError("fact limit must be between 1 and 500")
        async with self._session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(MemoryUnit, Document)
                        .join(Document, Document.id == MemoryUnit.document_id)
                        .where(
                            self._document_scope(),
                            MemoryUnit.bank_id == self.scope.bank_id,
                            MemoryUnit.state.in_(("active", "stale")),
                            MemoryUnit.is_source_chunk.is_(False),
                        )
                        .order_by(MemoryUnit.mentioned_at.desc())
                        .limit(limit)
                    )
                ).all()
            )
        return [
            {
                "id": str(row.id),
                "type": row.memory_type,
                "text": row.text,
                "freshness": row.state,
                "document_id": str(row.document_id),
                "document_title": document.title,
                "source_memory_ids": [str(item) for item in row.source_memory_ids],
                "mentioned_at": row.mentioned_at.isoformat(),
            }
            for row, document in rows
        ]

    async def observation_detail(self, observation_id: str) -> dict | None:
        try:
            identity = uuid.UUID(observation_id)
        except ValueError:
            return None
        async with self._session_factory() as session:
            pair = (
                await session.execute(
                    select(ObservationRecord, MemoryUnit)
                    .join(MemoryUnit, MemoryUnit.id == ObservationRecord.memory_id)
                    .join(Document, Document.id == MemoryUnit.document_id)
                    .where(
                        ObservationRecord.memory_id == identity,
                        self._document_scope(),
                        MemoryUnit.bank_id == self.scope.bank_id,
                    )
                )
            ).one_or_none()
            if pair is None:
                return None
            record, memory = pair
            history = list(
                await session.scalars(
                    select(ObservationHistory)
                    .where(
                        ObservationHistory.observation_id == identity,
                        ObservationHistory.bank_id == self.scope.bank_id,
                    )
                    .order_by(ObservationHistory.version.desc())
                )
            )
            facts = list(
                (
                    await session.execute(
                        select(ObservationEvidence, MemoryUnit)
                        .join(MemoryUnit, MemoryUnit.id == ObservationEvidence.fact_id)
                        .join(Document, Document.id == MemoryUnit.document_id)
                        .where(
                            ObservationEvidence.observation_id == identity,
                            ObservationEvidence.active.is_(True),
                            MemoryUnit.state == "active",
                            self._document_scope(),
                        )
                    )
                ).all()
            )
        return {
            "id": observation_id,
            "text": memory.text,
            "version": record.version,
            "freshness": record.freshness,
            "stale_reason": record.stale_reason,
            "has_conflict": record.has_conflict,
            "history": [
                {
                    "version": item.version,
                    "text": item.text,
                    "freshness": item.freshness,
                    "change_kind": item.change_kind,
                    "reason": item.reason,
                    "evidence_snapshot": item.evidence_snapshot,
                    "recorded_at": item.recorded_at.isoformat(),
                }
                for item in history
            ],
            "sources": [
                {
                    "id": str(fact.id),
                    "text": fact.text,
                    "type": fact.memory_type,
                    "version": edge.fact_version,
                    "document_id": str(fact.document_id),
                }
                for edge, fact in facts
            ],
        }

    @staticmethod
    def _duration(start, end):
        return (
            max(0, round((end - start).total_seconds() * 1000))
            if start and end
            else None
        )

    @staticmethod
    def _merge_status(left: str, right: str) -> str:
        order = {
            "failed": 5,
            "budget_exhausted": 4,
            "cancelled": 4,
            "processing": 3,
            "pending": 2,
            "completed": 1,
        }
        return max((left, right), key=lambda item: order.get(item, 0))
