"""Internal engine capability for conversation-memory operations."""

from __future__ import annotations

from typing import Protocol

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
)

# 截断标记：保留内容以该标记结尾，提示内容不完整。
TRUNCATION_MARKER = "\n\n[truncated]"


class ConversationQueue(Protocol):
    async def enqueue(
        self, *, session_id: str, turn_id: str, content: str, title: str | None = None
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
        max_turn_chars: int = 100_000,
    ) -> None:
        if max_recall_results < 1:
            raise ValueError("max_recall_results must be greater than zero")
        if max_turn_chars < 1:
            raise ValueError("max_turn_chars must be greater than zero")
        self._queue = queue
        self._recall_service = recall_service
        self._repository = repository
        self._max_recall_results = max_recall_results
        self._max_turn_chars = max_turn_chars

    async def recall_conversation_memory(
        self, request: ConversationMemoryRecallRequest
    ) -> ConversationMemoryRecallResult:
        query = request.query.strip()
        if not query:
            raise ValueError("query cannot be empty")
        if request.top_k < 1 or request.top_k > self._max_recall_results:
            raise ValueError(
                f"top_k must be between 1 and {self._max_recall_results}"
            )
        if request.mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {request.mode}")
        recalled = await self._recall_service.recall(
            query,
            mode=request.mode,
            top_k=request.top_k,
            source_type="conversation",
        )
        memories = []
        for item in recalled.results:
            if not item.session_id or not item.turn_id:
                continue
            memories.append(
                ConversationMemoryItem(
                    memory_id=item.id,
                    text=item.text,
                    memory_type=item.memory_type,
                    document_id=item.document_id,
                    session_id=item.session_id,
                    turn_id=item.turn_id,
                    score=item.final_score,
                    metadata=dict(item.metadata),
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
        # 内容上界：粘贴的巨文档不再整段落库（否则每轮触发 50+ 次
        # LLM 抽取），超出部分截断并附显式标记；恰好一次留存。
        content = f"[user]\n{user_text}\n\n[assistant]\n{assistant_text}"
        if len(content) > self._max_turn_chars:
            content = content[: self._max_turn_chars] + TRUNCATION_MARKER
        job = await self._queue.enqueue(
            session_id=session_id,
            turn_id=turn_id,
            content=content,
            title="Conversation turn",
        )
        return ConversationEnqueueResult(
            document_id=job.document_id,
            status=job.status,
        )

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
    *, max_recall_results: int = 20, max_turn_chars: int = 100_000
) -> ConversationMemoryService:
    repository = PostgresMemoryRepository()
    return ConversationMemoryService(
        PostgresConversationMemoryQueue(),
        HindsightService(repository, ProjectHindsightProviders()),
        repository,
        max_recall_results=max_recall_results,
        max_turn_chars=max_turn_chars,
    )
