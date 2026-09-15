"""Compatibility diagnostics for legacy document-retention states."""

from __future__ import annotations

from collections.abc import Mapping


_ACTIVE_STAGE_STATUSES = {"pending", "processing", "queued", "retaining"}


def document_state_diagnostic(
    status: str,
    error: str | None,
    stage_results: Mapping[str, object] | None,
) -> tuple[str, str | None]:
    """Expose legacy retry rows that no worker can consume as terminal.

    Older document-operation retries only changed the persisted state to
    ``pending``.  Unlike conversation jobs, document states have no queue
    consumer; a real retry must reingest the document.  Preserve genuinely
    new/active states, but recover rows whose previous stage results are all
    terminal so the UI does not claim that work is still running forever.
    """
    if status != "pending" or not stage_results:
        return status, error

    prior_stages = [
        str(value)
        for key, value in stage_results.items()
        if key != "retain" and value is not None
    ]
    if not prior_stages or any(
        value in _ACTIVE_STAGE_STATUSES for value in prior_stages
    ):
        return status, error

    recovered_status = "degraded" if "degraded" in prior_stages else "failed"
    return recovered_status, error or "legacy_document_retry_not_scheduled"
