"""TKB-owned Hindsight retain, recall, and reflect core.

This package contains only Hindsight-specific memory behaviour.  Document
ingestion, file extraction, database sessions, transports, and GraphRAG remain
owned by the existing project and are connected in later integration batches.
"""

from .config import HindsightOptions
from .service import HindsightService
from .types import RecallResult, ReflectResult, RetainResult

__all__ = [
    "HindsightOptions",
    "HindsightService",
    "RecallResult",
    "ReflectResult",
    "RetainResult",
]
