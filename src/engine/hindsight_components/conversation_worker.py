"""Leased worker for retaining queued conversation turns."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from src.engine.scope import MemoryScope, TagFilter
from typing import Protocol

from .conversation_queue import PostgresConversationMemoryQueue
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService
from .types import (
    ConversationMemoryJob,
    ConversationRetentionBatchResult,
    RetainInput,
    RetentionLeaseLost,
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

    async def complete(
        self, document_id: str, *, lease_token: str | None = None
    ) -> bool: ...

    async def fail(
        self,
        document_id: str,
        error_msg: str,
        *,
        max_attempts: int,
        retry_delay_seconds: float,
        lease_token: str | None = None,
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
        queue, service, cleaner = self._queue, self._service, self._memory_cleaner
        scope = MemoryScope(
            bank_id=job.bank_id,
            visibility=TagFilter(job.tags, "all_strict") if job.tags else None,
        )
        if hasattr(queue, "with_scope"):
            queue = queue.with_scope(scope)
            service = service.with_scope(scope)
            cleaner = cleaner.with_scope(scope)
        elif scope != MemoryScope():
            raise ValueError("worker dependencies do not support isolated scopes")
        if await queue.get_status(job.document_id) != "processing":
            return "cancelled"
        lease_args = {"lease_token": job.lease_token} if job.lease_token else {}
        if job.lease_token:
            service = service.with_lease(job.document_id, job.lease_token)
        try:
            retained = await service.retain(
                RetainInput(
                    document_id=job.document_id,
                    title=job.title,
                    content=job.content,
                    file_type="conversation",
                    source_type="conversation",
                    context=self._retention_context,
                    tags=tuple(
                        dict.fromkeys(
                            (*job.tags, "conversation", f"session:{job.session_id}")
                        )
                    ),
                    metadata={
                        "session_id": job.session_id,
                        "turn_id": job.turn_id,
                    },
                    agent_name=job.source_context.get("agent_name"),
                    speakers=job.source_context.get("speakers", {}),
                    source_timestamp=datetime.fromisoformat(
                        job.source_context["source_timestamp"]
                    )
                    if job.source_context.get("source_timestamp")
                    else None,
                    reference_timezone=job.source_context.get(
                        "reference_timezone", "UTC"
                    ),
                    policy_version=job.source_context.get("policy_version", 1),
                )
            )
            if job.lease_token and hasattr(queue, "record_stages"):
                await queue.record_stages(
                    job.document_id,
                    getattr(retained, "stage_results", {}),
                    **lease_args,
                )
            stage_results = getattr(retained, "stage_results", {}) or {}
            critical_stage_incomplete = any(
                value in {"degraded", "failed"}
                for stage, value in stage_results.items()
                if stage != "entities"
            )
            if (
                getattr(retained, "status", "success") == "failed"
                or critical_stage_incomplete
            ):
                raise RuntimeError("retention_stage_incomplete")
        except asyncio.CancelledError:
            raise
        except RetentionLeaseLost:
            return "cancelled"
        except Exception as error:
            delay = min(
                self._retry_delay_seconds * (2 ** max(job.attempts - 1, 0)),
                self._max_retry_delay_seconds,
            )
            return await queue.fail(
                job.document_id,
                f"{type(error).__name__}: retention_failed",
                max_attempts=self._max_attempts,
                retry_delay_seconds=delay,
                **lease_args,
            )

        if await queue.complete(job.document_id, **lease_args):
            return "completed"

        if job.lease_token:
            # A new worker may already have committed; stale cleanup must not
            # remove its result. Explicit forgetting owns cancellation cleanup.
            return "cancelled"

        try:
            await cleaner.delete_document(job.document_id)
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
    consolidation_enabled: bool = False,
) -> ConversationWorkerRuntime:
    repository = PostgresMemoryRepository(consolidation_enabled=consolidation_enabled)
    worker = ConversationRetentionWorker(
        PostgresConversationMemoryQueue(all_banks=True),
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
