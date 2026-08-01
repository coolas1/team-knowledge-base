"""TKB-owned Hindsight-style retain, recall, and reflect components.

The package is intentionally isolated from the engine factory and persistence
setup.  Importing it does not enable Hindsight or change GraphRAG behaviour.
"""

from .backend import HindsightBackend
from .contracts import DocumentRepository, HindsightMemory, StoredDocument

__all__ = [
    "DocumentRepository",
    "HindsightBackend",
    "HindsightMemory",
    "StoredDocument",
]
