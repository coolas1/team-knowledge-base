from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from src.engine.hindsight_components.graph_outbox import (
    GraphOutboxEvent,
    GraphProjectionWorker,
    PostgresGraphOutbox,
)
from src.engine.hindsight_components.graph_types import (
    MemoryGraphDocument,
    MemoryGraphProjection,
)
from src.engine.hindsight_components.models import HindsightGraphOutbox


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, row=None) -> None:
        self.row = row
        self.flushed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return AsyncContext()

    async def scalar(self, statement):
        return self.row

    async def get(self, model, event_id):
        if model is HindsightGraphOutbox and self.row and self.row.id == event_id:
            return self.row
        return None

    async def flush(self):
        self.flushed += 1


class FakeSessionFactory:
    def __init__(self, row=None) -> None:
        self.session = FakeSession(row)

    def __call__(self):
        return self.session


class FakeOutbox:
    def __init__(self, events: list[GraphOutboxEvent]) -> None:
        self.events = list(events)
        self.completed: list[int] = []
        self.failed: list[tuple[int, str, int]] = []

    async def claim(self, **kwargs):
        return self.events.pop(0) if self.events else None

    async def complete(self, event_id: int) -> None:
        self.completed.append(event_id)

    async def fail(
        self,
        event_id: int,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> None:
        self.failed.append((event_id, error, retry_delay_seconds))


class FakeSource:
    def __init__(self, values: dict[str, MemoryGraphProjection | None]) -> None:
        self.values = values
        self.calls: list[str] = []

    async def graph_projection(self, document_id: str):
        self.calls.append(document_id)
        return self.values.get(document_id)


class FakeProjector:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.replaced: list[MemoryGraphProjection] = []
        self.deleted: list[str] = []

    async def replace_document(self, value: MemoryGraphProjection) -> None:
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.replaced.append(value)

    async def delete_document(self, document_id: str) -> None:
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.deleted.append(document_id)


def outbox_row() -> HindsightGraphOutbox:
    row = HindsightGraphOutbox(
        document_id=uuid.uuid4(),
        operation="replace",
        status="pending",
        attempts=0,
        available_at=datetime.now(timezone.utc),
    )
    row.id = 7
    return row


def graph_projection(document_id: str) -> MemoryGraphProjection:
    return MemoryGraphProjection(
        document=MemoryGraphDocument(
            id=document_id,
            title="week.md",
            file_type="markdown",
        )
    )


async def test_postgres_outbox_claim_sets_processing_lease():
    row = outbox_row()
    factory = FakeSessionFactory(row)
    outbox = PostgresGraphOutbox(factory)

    event = await outbox.claim(lease_seconds=60, max_attempts=3)

    assert event is not None
    assert event.id == 7
    assert event.document_id == str(row.document_id)
    assert event.attempts == 1
    assert row.status == "processing"
    assert row.locked_at is not None
    assert factory.session.flushed == 1


def test_claim_statement_serializes_events_per_document():
    now = datetime.now(timezone.utc)
    statement = PostgresGraphOutbox._claim_statement(now, now, 10)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()

    assert "not (exists" in sql
    assert "document_id" in sql
    assert "attempts < 10" in sql
    assert "for update skip locked" in sql


async def test_postgres_outbox_complete_and_fail_update_durable_state():
    row = outbox_row()
    factory = FakeSessionFactory(row)
    outbox = PostgresGraphOutbox(factory)

    await outbox.fail(7, "neo4j unavailable", retry_delay_seconds=4)
    assert row.status == "failed"
    assert row.error_msg == "neo4j unavailable"
    assert row.locked_at is None
    retry_at = row.available_at

    await outbox.complete(7)
    assert row.status == "completed"
    assert row.error_msg is None
    assert row.available_at == retry_at


async def test_worker_projects_replace_and_completes_event():
    document_id = str(uuid.uuid4())
    event = GraphOutboxEvent(1, document_id, "replace", 1)
    value = graph_projection(document_id)
    outbox = FakeOutbox([event])
    source = FakeSource({document_id: value})
    projector = FakeProjector()

    result = await GraphProjectionWorker(outbox, source, projector).run_once()

    assert result is not None and result.status == "completed"
    assert projector.replaced == [value]
    assert outbox.completed == [1]
    assert outbox.failed == []


async def test_worker_turns_stale_replace_into_delete():
    document_id = str(uuid.uuid4())
    outbox = FakeOutbox([GraphOutboxEvent(2, document_id, "replace", 1)])
    source = FakeSource({document_id: None})
    projector = FakeProjector()

    result = await GraphProjectionWorker(outbox, source, projector).run_once()

    assert result is not None and result.status == "completed"
    assert projector.deleted == [document_id]
    assert outbox.completed == [2]


async def test_worker_processes_delete_without_loading_postgres_projection():
    document_id = str(uuid.uuid4())
    outbox = FakeOutbox([GraphOutboxEvent(3, document_id, "delete", 1)])
    source = FakeSource({})
    projector = FakeProjector()

    result = await GraphProjectionWorker(outbox, source, projector).run_once()

    assert result is not None and result.status == "completed"
    assert source.calls == []
    assert projector.deleted == [document_id]


async def test_worker_records_failure_with_bounded_exponential_retry():
    document_id = str(uuid.uuid4())
    outbox = FakeOutbox([GraphOutboxEvent(4, document_id, "replace", 3)])
    source = FakeSource({document_id: graph_projection(document_id)})
    projector = FakeProjector(fail=True)

    result = await GraphProjectionWorker(outbox, source, projector).run_once()

    assert result is not None and result.status == "failed"
    assert result.error == "neo4j unavailable"
    assert outbox.completed == []
    assert outbox.failed == [(4, "neo4j unavailable", 8)]


async def test_worker_drain_stops_when_queue_is_empty():
    first = str(uuid.uuid4())
    second = str(uuid.uuid4())
    outbox = FakeOutbox(
        [
            GraphOutboxEvent(5, first, "delete", 1),
            GraphOutboxEvent(6, second, "delete", 1),
        ]
    )
    worker = GraphProjectionWorker(outbox, FakeSource({}), FakeProjector())

    results = await worker.drain(limit=10)

    assert [result.event_id for result in results] == [5, 6]
