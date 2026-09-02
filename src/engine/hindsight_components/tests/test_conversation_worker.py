from __future__ import annotations

import asyncio

import pytest

from src.engine.hindsight_components.conversation_worker import (
    ConversationRetentionWorker,
    ConversationWorkerRuntime,
)
from src.engine.hindsight_components.types import (
    ConversationMemoryJob,
    ConversationRetentionBatchResult,
    RetainInput,
)


def _job(*, document_id: str = "document-1", attempts: int = 1):
    return ConversationMemoryJob(
        document_id=document_id,
        session_id="session-1",
        turn_id=f"turn-{document_id}",
        title="Conversation turn",
        content="[user]\nRemember blue.\n\n[assistant]\nUnderstood.",
        attempts=attempts,
        status="processing",
    )


class FakeQueue:
    def __init__(self, jobs: list[ConversationMemoryJob]) -> None:
        self.jobs = jobs
        self.statuses = {job.document_id: "processing" for job in jobs}
        self.claim_args = None
        self.failures: list[tuple[str, str, int, float]] = []

    async def claim(self, *, limit, lease_seconds, max_attempts):
        self.claim_args = (limit, lease_seconds, max_attempts)
        return self.jobs[:limit]

    async def get_status(self, document_id):
        return self.statuses.get(document_id)

    async def complete(self, document_id):
        if self.statuses.get(document_id) != "processing":
            return False
        self.statuses[document_id] = "completed"
        return True

    async def fail(
        self,
        document_id,
        error_msg,
        *,
        max_attempts,
        retry_delay_seconds,
    ):
        self.failures.append(
            (document_id, error_msg, max_attempts, retry_delay_seconds)
        )
        job = next(item for item in self.jobs if item.document_id == document_id)
        status = "failed" if job.attempts >= max_attempts else "pending"
        self.statuses[document_id] = status
        return status


class FakeService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.inputs: list[RetainInput] = []

    async def retain(self, retain_input: RetainInput):
        self.inputs.append(retain_input)
        if self.fail:
            raise RuntimeError("provider unavailable")


class FakeCleaner:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete_document(self, document_id: str) -> None:
        self.deleted.append(document_id)


async def test_worker_retains_conversation_provenance_and_completes_job() -> None:
    queue = FakeQueue([_job()])
    service = FakeService()
    worker = ConversationRetentionWorker(queue, service, FakeCleaner())

    result = await worker.run_once()

    assert result == ConversationRetentionBatchResult(claimed=1, completed=1)
    retain_input = service.inputs[0]
    assert retain_input.source_type == "conversation"
    assert retain_input.metadata == {"session_id": "session-1", "turn_id": "turn-document-1"}
    assert "session:session-1" in retain_input.tags


@pytest.mark.parametrize(
    ("attempts", "expected_status", "expected_delay"),
    [(2, "pending", 4.0), (3, "failed", 8.0)],
)
async def test_worker_retries_provider_failure_with_bounded_backoff(
    attempts, expected_status, expected_delay
) -> None:
    queue = FakeQueue([_job(attempts=attempts)])
    worker = ConversationRetentionWorker(
        queue,
        FakeService(fail=True),
        FakeCleaner(),
        max_attempts=3,
        retry_delay_seconds=2,
        max_retry_delay_seconds=10,
    )

    result = await worker.run_once()

    assert getattr(result, "retried" if expected_status == "pending" else "failed") == 1
    assert queue.failures[0][2:] == (3, expected_delay)


async def test_worker_bounds_concurrency_and_passes_lease_recovery_settings() -> None:
    jobs = [_job(document_id=f"document-{index}", attempts=2) for index in range(3)]
    queue = FakeQueue(jobs)
    active = 0
    maximum = 0

    class ConcurrentService(FakeService):
        async def retain(self, retain_input: RetainInput):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

    result = await ConversationRetentionWorker(
        queue,
        ConcurrentService(),
        FakeCleaner(),
        max_concurrent=2,
        lease_seconds=45,
        max_attempts=4,
    ).run_once()

    assert result.claimed == 2
    assert result.completed == 2
    assert maximum == 2
    assert queue.claim_args == (2, 45, 4)


async def test_worker_cleans_memories_when_session_is_forgotten_during_retain() -> None:
    job = _job()
    queue = FakeQueue([job])
    cleaner = FakeCleaner()

    class CancellingService(FakeService):
        async def retain(self, retain_input: RetainInput):
            queue.statuses[job.document_id] = "cancelled"

    result = await ConversationRetentionWorker(
        queue, CancellingService(), cleaner
    ).run_once()

    assert result.cancelled == 1
    assert cleaner.deleted == [job.document_id]


async def test_runtime_stops_cleanly_after_current_iteration() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Worker:
        async def run_once(self):
            entered.set()
            await release.wait()
            return ConversationRetentionBatchResult()

    runtime = ConversationWorkerRuntime(Worker(), poll_seconds=0.01)
    await runtime.start()
    await asyncio.wait_for(entered.wait(), timeout=1)
    stop_task = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    release.set()
    await stop_task
    assert runtime._task is None
