from __future__ import annotations

import asyncio

from src.engine.hindsight_components.hook import HindsightRetainHook
from src.engine.hindsight_components.types import RetainInput


class FakeRepository:
    def __init__(self) -> None:
        self.states: list[tuple[str, str, str | None]] = []
        self.deleted: list[str] = []
        self.fail_delete = False

    async def set_document_state(
        self,
        document_id: str,
        status: str,
        *,
        error_msg: str | None = None,
    ) -> None:
        self.states.append((document_id, status, error_msg))

    async def delete_document(self, document_id: str) -> None:
        if self.fail_delete:
            raise RuntimeError("delete unavailable")
        self.deleted.append(document_id)


class FakeService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.retained: list[RetainInput] = []

    async def retain(self, retain_input: RetainInput):
        self.retained.append(retain_input)
        if self.fail:
            raise RuntimeError("LLM unavailable")


async def test_hook_retains_text_from_primary_pipeline() -> None:
    repository = FakeRepository()
    service = FakeService()
    hook = HindsightRetainHook(service, repository)

    await hook.after_indexed(
        document_id="document-1",
        title="week.md",
        content="weekly report",
        file_type="markdown",
    )

    assert repository.states == [("document-1", "retaining", None)]
    assert service.retained == [
        RetainInput(
            document_id="document-1",
            title="week.md",
            content="weekly report",
            file_type="markdown",
            source_type="graphrag-pipeline",
        )
    ]


async def test_hook_records_failure_without_raising() -> None:
    repository = FakeRepository()
    hook = HindsightRetainHook(FakeService(fail=True), repository)

    await hook.after_indexed(
        document_id="document-1",
        title="week.md",
        content="weekly report",
        file_type="markdown",
    )

    assert repository.states[-1] == (
        "document-1",
        "failed",
        "LLM unavailable",
    )


async def test_hook_limits_retain_concurrency() -> None:
    repository = FakeRepository()
    active = 0
    maximum = 0

    class ConcurrentService(FakeService):
        async def retain(self, retain_input: RetainInput):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

    hook = HindsightRetainHook(ConcurrentService(), repository, max_concurrent=1)
    await asyncio.gather(
        *(
            hook.after_indexed(
                document_id=f"document-{index}",
                title="week.md",
                content="content",
                file_type="markdown",
            )
            for index in range(3)
        )
    )

    assert maximum == 1


async def test_delete_failure_is_isolated() -> None:
    repository = FakeRepository()
    repository.fail_delete = True
    hook = HindsightRetainHook(FakeService(), repository)

    await hook.before_remove("document-1")
