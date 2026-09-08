"""In-memory pipeline progress tracker (per doc_id).

The pipeline runs as a background ``asyncio.create_task`` that does not survive
a process restart, so an in-memory dict is the right store - no DB migration
needed. The host BFF reads from here to surface stage/elapsed info to the SPA.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PipelineProgress:
    """Snapshot of where the pipeline is for one document."""

    stage: str  # extracting|chunking|overview|analyzing_chunks|embedding|writing_postgres|writing_neo4j
    detail: str = ""
    current: int = 0  # e.g. chunk 3 of 10
    total: int = 0  # e.g. 10 chunks (0 = indeterminate)
    started_at: float = 0.0  # epoch seconds
    updated_at: float = 0.0  # epoch seconds


_progress: dict[str, PipelineProgress] = {}


def set_progress(
    doc_id: str,
    stage: str,
    detail: str = "",
    current: int = 0,
    total: int = 0,
) -> None:
    """Update (or create) the progress entry for *doc_id*."""
    now = time.time()
    existing = _progress.get(doc_id)
    if existing is None:
        _progress[doc_id] = PipelineProgress(
            stage=stage,
            detail=detail,
            current=current,
            total=total,
            started_at=now,
            updated_at=now,
        )
    else:
        existing.stage = stage
        existing.detail = detail
        existing.current = current
        existing.total = total
        existing.updated_at = now


def get_progress(doc_id: str) -> PipelineProgress | None:
    return _progress.get(doc_id)


def clear_progress(doc_id: str) -> None:
    _progress.pop(doc_id, None)
