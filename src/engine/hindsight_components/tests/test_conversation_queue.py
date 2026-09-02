from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.engine.hindsight_components.conversation_queue import (
    CONVERSATION_FILE_TYPE,
    PostgresConversationMemoryQueue,
    conversation_document_id,
)


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _Result:
    def __init__(self, *, one=None, rows=None, rowcount=0):
        self._one = one
        self._rows = rows or []
        self.rowcount = rowcount

    def one(self):
        return self._one

    def all(self):
        return self._rows


def _source(**overrides):
    values = {
        "document_id": uuid.uuid4(),
        "session_id": "session-1",
        "turn_id": "turn-1",
        "status": "pending",
        "attempts": 0,
        "error_msg": None,
        "locked_at": None,
        "available_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _document(source, **overrides):
    values = {
        "id": source.document_id,
        "title": "Conversation turn",
        "raw_text": "[user]\nHello\n\n[assistant]\nHi",
        "file_type": CONVERSATION_FILE_TYPE,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_conversation_document_id_is_stable_and_validates_input() -> None:
    first = conversation_document_id("session-1", "turn-1")
    second = conversation_document_id("session-1", "turn-1")

    assert first == second
    assert first != conversation_document_id("session-1", "turn-2")
    with pytest.raises(ValueError, match="must not be empty"):
        conversation_document_id("", "turn-1")


async def test_enqueue_uses_idempotent_upserts_and_returns_source_job() -> None:
    source = _source(document_id=conversation_document_id("session-1", "turn-1"))
    document = _document(source)

    class Session:
        def __init__(self):
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return _Context(self)

        async def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 3:
                return _Result(one=(source, document))
            return _Result()

    session = Session()
    queue = PostgresConversationMemoryQueue(lambda: session)

    result = await queue.enqueue(
        session_id="session-1",
        turn_id="turn-1",
        content=document.raw_text,
    )

    sql = [
        str(statement.compile(dialect=postgresql.dialect())).lower()
        for statement in session.statements[:2]
    ]
    assert all("on conflict" in statement for statement in sql)
    assert result.document_id == str(source.document_id)
    assert result.session_id == "session-1"
    assert result.content == document.raw_text


async def test_claim_recovers_expired_work_and_increments_attempt() -> None:
    source = _source(status="processing", attempts=2)
    document = _document(source)

    class Session:
        def __init__(self):
            self.statement = None
            self.flushed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return _Context(self)

        async def execute(self, statement):
            self.statement = statement
            return _Result(rows=[(source, document)])

        async def flush(self):
            self.flushed = True

    session = Session()
    jobs = await PostgresConversationMemoryQueue(lambda: session).claim(
        lease_seconds=30,
        max_attempts=4,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    sql = str(session.statement.compile(dialect=postgresql.dialect())).lower()
    assert "for update skip locked" in sql
    assert "attempts" in sql and "locked_at" in sql
    assert source.status == "processing"
    assert source.attempts == 3
    assert jobs[0].attempts == 3
    assert session.flushed is True


@pytest.mark.parametrize(
    ("attempts", "initial_status", "expected"),
    [
        (1, "processing", "pending"),
        (3, "processing", "failed"),
        (1, "cancelled", "cancelled"),
    ],
)
async def test_fail_retries_bounds_attempts_and_preserves_cancellation(
    attempts, initial_status, expected
) -> None:
    source = _source(attempts=attempts, status=initial_status)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return _Context(self)

        async def scalar(self, _statement):
            return source

    status = await PostgresConversationMemoryQueue(lambda: Session()).fail(
        str(source.document_id),
        "provider unavailable",
        max_attempts=3,
        retry_delay_seconds=2,
        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert status == expected
    assert source.status == expected
    if expected == "cancelled":
        assert source.error_msg is None
    else:
        assert source.error_msg == "provider unavailable"


async def test_complete_cancel_and_status_counts_map_database_results() -> None:
    class Session:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return _Result(rowcount=1)
            if self.calls == 2:
                return _Result(rowcount=2)
            return _Result(rows=[("pending", 3), ("failed", 1)])

        async def commit(self):
            return None

    session = Session()
    queue = PostgresConversationMemoryQueue(lambda: session)

    assert await queue.complete(str(uuid.uuid4())) is True
    assert await queue.cancel_session("session-1") == 2
    stats = await queue.status_counts()

    assert stats.pending == 3
    assert stats.failed == 1
    assert stats.processing == 0
    assert stats.total == 4
