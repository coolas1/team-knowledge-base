from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

from src.engine.hindsight_components.document_diagnostics import (
    document_state_diagnostic,
)
from src.engine.hindsight_components.memory_admin import _consolidation_diagnostic
from src.engine.hindsight_components.repository import PostgresMemoryRepository


def test_consolidation_diagnostic_marks_abandoned_processing_as_failed() -> None:
    now = datetime.now(timezone.utc)

    assert _consolidation_diagnostic("processing", None, None, now=now) == (
        "failed",
        "processing_lease_expired",
    )
    assert _consolidation_diagnostic(
        "processing", None, now - timedelta(seconds=1), now=now
    ) == ("failed", "processing_lease_expired")


def test_consolidation_diagnostic_preserves_live_and_terminal_states() -> None:
    now = datetime.now(timezone.utc)

    assert _consolidation_diagnostic(
        "processing", None, now + timedelta(seconds=1), now=now
    ) == ("processing", None)
    assert _consolidation_diagnostic(
        "processing", "cancelled_by_admin", None, now=now
    ) == ("cancelled", "cancelled_by_admin")
    assert _consolidation_diagnostic("completed", None, None, now=now) == (
        "completed",
        None,
    )


def test_document_diagnostic_recovers_legacy_pending_retry() -> None:
    stages = {
        "chunk:0": "degraded",
        "extract": "degraded",
        "entities": "success",
        "consolidate": "empty",
    }

    assert document_state_diagnostic("pending", None, stages) == (
        "degraded",
        "legacy_document_retry_not_scheduled",
    )


def test_document_diagnostic_preserves_active_or_new_pending_state() -> None:
    assert document_state_diagnostic("pending", None, {}) == ("pending", None)
    assert document_state_diagnostic(
        "pending", None, {"extract": "processing"}
    ) == ("pending", None)
    assert document_state_diagnostic(
        "degraded", "provider_timeout", {"extract": "degraded"}
    ) == ("degraded", "provider_timeout")


def test_document_state_reader_normalizes_legacy_pending_retry() -> None:
    row = SimpleNamespace(
        document_id=uuid.uuid4(),
        status="pending",
        error_msg=None,
        stage_results={"extract": "degraded", "consolidate": "empty"},
        memory_count=1,
        link_count=0,
        updated_at=datetime.now(timezone.utc),
    )

    state = PostgresMemoryRepository._state_from_row(row)

    assert state.status == "degraded"
    assert state.error_msg == "legacy_document_retry_not_scheduled"
