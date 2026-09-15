from datetime import datetime, timedelta, timezone

from src.engine.hindsight_components.memory_admin import _consolidation_diagnostic


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
