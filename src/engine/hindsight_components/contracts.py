"""Internal ports used by the Hindsight backend adapter.

These protocols keep the public ``KnowledgeBase`` adapter independent from the
source repository's SQLAlchemy session and ORM layout.  The next persistence
phase can implement them without changing the adapter or its callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(slots=True)
class StoredDocument:
    """Persistence-neutral document representation used by the adapter."""

    id: str
    title: str
    file_type: str
    status: str
    raw_text: str
    overview: str = ""
    error_msg: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DocumentRepository(Protocol):
    """Document operations required by :class:`HindsightBackend`."""

    async def create(
        self, *, title: str, file_type: str, raw_text: str
    ) -> StoredDocument: ...

    async def get(self, doc_id: str) -> StoredDocument | None: ...

    async def set_status(
        self, doc_id: str, status: str, *, error_msg: str | None = None
    ) -> StoredDocument: ...

    async def delete(self, doc_id: str) -> None: ...

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        file_type: str | None,
        status: str | None,
    ) -> tuple[int, list[StoredDocument]]: ...


class HindsightMemory(Protocol):
    """Public subset of the existing ``TkbMemory`` implementation."""

    async def retain_document(
        self,
        *,
        document_id: str,
        title: str,
        content: str,
        file_type: str,
        source_type: str,
    ) -> dict[str, Any]: ...

    async def delete_document(
        self, document_id: str, *, missing_ok: bool = True
    ) -> bool: ...

    async def recall(self, query: str, mode: str = "deep") -> dict[str, Any]: ...

    async def entity_graph(self) -> dict[str, Any]: ...

    async def get_entity_by_name(self, name: str) -> dict[str, Any] | None: ...


class SourceExtractor(Protocol):
    """Converts an ingest source into text understood by the memory engine."""

    async def extract(
        self, name: str, data: bytes, path: str | None
    ) -> tuple[str, str]:
        """Return ``(raw_text, file_type)``."""
