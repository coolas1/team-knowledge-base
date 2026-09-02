"""Leased worker for retaining queued conversation turns."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .conversation_queue import PostgresConversationMemoryQueue
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService
from .types import (
    ConversationMemoryJob,
    ConversationRetentionBatchResult,
    RetainInput,
)

logger = logging.getLogger(__name__)


class ConversationQueue(Protocol):
    async def claim(
        self,
        *,
        limit: int,
        lease_seconds: int,
        max_attempts: int,
    ) -> list[ConversationMemoryJob]: ...

    async def get_status(self, document_id: str) -> str | None: ...

    async def complete(self, document_id: str) -> bool: ...

    async def fail(
        self,
        document_id: str,
        error_msg: str,
        *,
        max_attempts: int,
        retry_delay_seconds: float,
    ) -> str: ...


class RetainService(Protocol):
    async def retain(self, retain_input: RetainInput): ...


class MemoryCleaner(Protocol):
    async def delete_document(self, document_id: str) -> None: ...


class ConversationRetentionWorker:
    def __init__(
        self,
        queue: ConversationQueue,
        service: RetainService,
        memory_cleaner: MemoryCleaner,
        *,
        max_concurrent: int = 1,
        lease_seconds: int = 300,
        max_attempts: int = 10,
        retry_delay_seconds: float = 1.0,
        max_retry_delay_seconds: float = 300.0,
        retention_context: str = "Completed team conversation turn",
    ) -> None:
        if max_concurrent < 1 or lease_seconds < 1 or max_attempts < 1:
            raise ValueError("worker limits must be greater than zero")
        if retry_delay_seconds < 0 or max_retry_delay_seconds < retry_delay_seconds:
            raise ValueError("worker retry delays are invalid")
        self._queue = queue
        self._service = service
        self._memory_cleaner = memory_cleaner
        self._max_concurrent = max_concurrent
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._max_retry_delay_seconds = max_retry_delay_seconds
        self._retention_context = retention_context

    async def run_once(self) -> ConversationRetentionBatchResult:
        jobs = await self._queue.claim(
            limit=self._max_concurrent,
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        if not jobs:
            return ConversationRetentionBatchResult()
        outcomes = await asyncio.gather(*(self._process(job) for job in jobs))
        return ConversationRetentionBatchResult(
            claimed=len(jobs),
            completed=outcomes.count("completed"),
            retried=outcomes.count("pending"),
            failed=outcomes.count("failed"),
            cancelled=outcomes.count("cancelled"),
        )

    async def _process(self, job: ConversationMemoryJob) -> str:
        if await self._queue.get_status(job.document_id) != "processing":
            return "cancelled"
        try:
            await self._service.retain(
                RetainInput(
                    document_id=job.document_id,
                    title=job.title,
                    content=job.content,
                    file_type="conversation",
                    source_type="conversation",
                    context=self._retention_context,
                    tags=("conversation", f"session:{job.session_id}"),
                    metadata={
                        "session_id": job.session_id,
                        "turn_id": job.turn_id,
                    },
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            delay = min(
                self._retry_delay_seconds * (2 ** max(job.attempts - 1, 0)),
                self._max_retry_delay_seconds,
            )
            return await self._queue.fail(
                job.document_id,
                str(error),
                max_attempts=self._max_attempts,
                retry_delay_seconds=delay,
            )

        if await self._queue.complete(job.document_id):
            return "completed"

        try:
            await self._memory_cleaner.delete_document(job.document_id)
        except Exception:
            logger.exception(
                "Failed to clean cancelled conversation memory %s", job.document_id
            )
        return "cancelled"


class ConversationWorkerRuntime:
    def __init__(
        self,
        worker: ConversationRetentionWorker,
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._worker = worker
        self._poll_seconds = poll_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(), name="hindsight-conversation-worker"
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        try:
            await task
        finally:
            self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = await self._worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Conversation retention worker iteration failed")
                result = ConversationRetentionBatchResult()
            if result.claimed == 0:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._poll_seconds
                    )
                except TimeoutError:
                    pass


def build_conversation_worker_runtime(
    *,
    poll_seconds: float = 1.0,
    max_concurrent: int = 1,
    lease_seconds: int = 300,
    max_attempts: int = 10,
    retry_delay_seconds: float = 1.0,
    max_retry_delay_seconds: float = 300.0,
    retention_context: str = "Completed team conversation turn",
) -> ConversationWorkerRuntime:
    repository = PostgresMemoryRepository()
    worker = ConversationRetentionWorker(
        PostgresConversationMemoryQueue(),
        HindsightService(repository, ProjectHindsightProviders()),
        repository,
        max_concurrent=max_concurrent,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
        retry_delay_seconds=retry_delay_seconds,
        max_retry_delay_seconds=max_retry_delay_seconds,
        retention_context=retention_context,
    )
    return ConversationWorkerRuntime(worker, poll_seconds=poll_seconds)
