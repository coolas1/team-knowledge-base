"""Internal engine capability for conversation-memory operations."""

from __future__ import annotations

from typing import Protocol
from hashlib import sha256
import json

from src.engine.interface import (
    ConversationEnqueueResult,
    ConversationForgetRequest,
    ConversationForgetResult,
    ConversationMemoryDiagnostics,
    ConversationMemoryItem,
    ConversationMemoryRecallRequest,
    ConversationMemoryRecallResult,
    ConversationTurn,
)

from .conversation_queue import PostgresConversationMemoryQueue
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService
from .types import (
    ConversationMemoryJob,
    ConversationMemoryQueueStats,
    RecallResult,
    RecallFilter,
)


class ConversationQueue(Protocol):
    async def enqueue(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        title: str | None = None,
        request_fingerprint: str | None = None,
        source_context: dict | None = None,
    ) -> ConversationMemoryJob: ...

    async def session_document_ids(self, session_id: str) -> list[str]: ...

    async def cancel_session(self, session_id: str) -> int: ...

    async def delete_documents(self, document_ids: list[str]) -> int: ...

    async def status_counts(self) -> ConversationMemoryQueueStats: ...


class RecallService(Protocol):
    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        source_type: str | None = None,
    ) -> RecallResult: ...


class MemoryRepository(Protocol):
    async def delete_document(self, document_id: str) -> None: ...


class ConversationMemoryService:
    def __init__(
        self,
        queue: ConversationQueue,
        recall_service: RecallService,
        repository: MemoryRepository,
        *,
        max_recall_results: int = 20,
    ) -> None:
        if max_recall_results < 1:
            raise ValueError("max_recall_results must be greater than zero")
        self._queue = queue
        self._recall_service = recall_service
        self._repository = repository
        self._max_recall_results = max_recall_results

    def with_scope(self, scope, *, write_tags=()):
        return ConversationMemoryService(
            self._queue.with_scope(scope, write_tags=write_tags),
            self._recall_service.with_scope(scope),
            self._repository.with_scope(scope),
            max_recall_results=self._max_recall_results,
        )

    async def recall_conversation_memory(
        self, request: ConversationMemoryRecallRequest
    ) -> ConversationMemoryRecallResult:
        query = request.query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        if request.top_k < 1 or request.top_k > self._max_recall_results:
            raise ValueError(f"top_k must be between 1 and {self._max_recall_results}")
        if request.mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {request.mode}")
        filter_arg = (
            {
                "filters": RecallFilter(
                    memory_types=request.memory_types,
                    source_types=("conversation",),
                )
            }
            if request.memory_types
            else {}
        )
        recalled = await self._recall_service.recall(
            query,
            mode=request.mode,
            top_k=request.top_k,
            source_type="conversation",
            **filter_arg,
        )
        memories = []
        for item in recalled.results:
            if (
                not item.session_id or not item.turn_id
            ) and item.memory_type != "observation":
                continue
            memories.append(
                ConversationMemoryItem(
                    memory_id=item.id,
                    text=item.text,
                    memory_type=item.memory_type,
                    document_id=item.document_id,
                    session_id=item.session_id or "derived",
                    turn_id=item.turn_id or item.id,
                    score=item.final_score,
                    metadata=dict(item.metadata),
                    mentioned_at=item.mentioned_at
                    if request.include_source_time
                    else None,
                    occurred_start=item.occurred_start
                    if request.include_source_time
                    else None,
                    occurred_end=item.occurred_end
                    if request.include_source_time
                    else None,
                )
            )
        return ConversationMemoryRecallResult(
            memories=memories,
            trace=dict(recalled.trace),
        )

    async def enqueue_conversation_turn(
        self, turn: ConversationTurn
    ) -> ConversationEnqueueResult:
        session_id = turn.session_id.strip()
        turn_id = turn.turn_id.strip()
        user_text = turn.user_text.strip()
        assistant_text = turn.assistant_text.strip()
        if not session_id or not turn_id:
            raise ValueError("session_id and turn_id must not be empty")
        if not user_text or not assistant_text:
            raise ValueError("user_text and assistant_text must not be empty")
        from src.engine.scope import MemoryScope
        from .types import RetainInput
        from datetime import datetime

        scope = getattr(self._queue, "scope", MemoryScope())
        timestamp = (
            datetime.fromisoformat(turn.source_timestamp.replace("Z", "+00:00"))
            if turn.source_timestamp
            else None
        )
        if turn.source_timestamp is not None and timestamp is None:
            raise ValueError("invalid source timestamp")
        RetainInput(
            document_id="validation",
            title="",
            content="",
            file_type="conversation",
            source_timestamp=timestamp,
            reference_timezone=turn.reference_timezone,
        )
        job = await self._queue.enqueue(
            session_id=session_id,
            turn_id=turn_id,
            content=(f"[user]\n{user_text}\n\n[assistant]\n{assistant_text}"),
            title="Conversation turn",
            source_context={
                "source_timestamp": timestamp.isoformat() if timestamp else None,
                "reference_timezone": turn.reference_timezone,
                "policy_version": scope.policy_version,
                "agent_name": scope.agent_name,
                "speakers": {"user": scope.subject_id, "assistant": scope.agent_name},
            },
            request_fingerprint=sha256(
                json.dumps(
                    [turn.user_text, turn.assistant_text],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        return ConversationEnqueueResult(
            document_id=job.document_id,
            status=job.status,
            operation_id=job.operation_id,
        )

    async def retry_extraction(
        self, document_id: str, *, stage: str = "extract"
    ) -> bool:
        return await self._queue.retry_stage(document_id, stage=stage)

    async def forget_conversation_memory(
        self, request: ConversationForgetRequest
    ) -> ConversationForgetResult:
        session_id = request.session_id.strip()
        if not session_id:
            raise ValueError("session_id must not be empty")
        document_ids = await self._queue.session_document_ids(session_id)
        cancelled = await self._queue.cancel_session(session_id)
        for document_id in document_ids:
            await self._repository.delete_document(document_id)
        deleted = await self._queue.delete_documents(document_ids)
        return ConversationForgetResult(
            session_id=session_id,
            cancelled_jobs=cancelled,
            deleted_documents=deleted,
        )

    async def conversation_memory_diagnostics(
        self,
    ) -> ConversationMemoryDiagnostics:
        stats = await self._queue.status_counts()
        return ConversationMemoryDiagnostics(
            enabled=True,
            pending=stats.pending,
            processing=stats.processing,
            completed=stats.completed,
            failed=stats.failed,
            cancelled=stats.cancelled,
        )


def build_conversation_memory_service(
    *, max_recall_results: int = 20, consolidation_enabled: bool = False
) -> ConversationMemoryService:
    repository = PostgresMemoryRepository(consolidation_enabled=consolidation_enabled)
    return ConversationMemoryService(
        PostgresConversationMemoryQueue(),
        HindsightService(repository, ProjectHindsightProviders()),
        repository,
        max_recall_results=max_recall_results,
    )
