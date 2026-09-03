"""Enrich DocumentRef/document dicts with memory (Hindsight) state."""
from __future__ import annotations

import logging
from typing import Any, Protocol

from src.engine.interface import DocumentRef
from src.engine.hindsight_components.types import DocumentMemoryState

logger = logging.getLogger(__name__)


class DocumentStateReader(Protocol):
    async def document_state(self, document_id: str) -> DocumentMemoryState | None: ...

    async def document_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]: ...


class MemoryStateEnricher:
    """Failure-isolated enrichment; never raises into the caller."""

    def __init__(self, reader: DocumentStateReader) -> None:
        self._reader = reader

    async def enrich_ref(self, ref: DocumentRef) -> None:
        self._apply(ref, await self.safe_state(ref.id), ref.status)

    async def enrich_dict(self, document: dict[str, Any]) -> None:
        state = await self.safe_state(str(document.get("id", "")))
        document.update(self._memory_values(str(document.get("status", "")), state))

    async def enrich_dicts(self, documents: list[dict[str, Any]]) -> None:
        ids = [str(d.get("id")) for d in documents if d.get("id")]
        states = await self.safe_states(ids)
        for d in documents:
            state = states.get(str(d.get("id", "")))
            d.update(self._memory_values(str(d.get("status", "")), state))

    async def safe_state(self, document_id: str) -> DocumentMemoryState | None:
        try:
            return await self._reader.document_state(document_id)
        except Exception:
            logger.exception("Failed to read memory state for %s", document_id)
            return DocumentMemoryState(document_id=document_id, status="unavailable")

    async def safe_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]:
        try:
            return await self._reader.document_states(document_ids)
        except Exception:
            logger.exception("Failed to read memory states")
            return {
                i: DocumentMemoryState(document_id=i, status="unavailable")
                for i in document_ids
            }

    def _apply(
        self,
        ref: DocumentRef,
        state: DocumentMemoryState | None,
        document_status: str,
    ) -> None:
        values = self._memory_values(document_status, state)
        ref.memory_status = values["memory_status"]
        ref.memory_error_msg = values["memory_error_msg"]
        ref.memory_count = values["memory_count"]
        ref.memory_link_count = values["memory_link_count"]

    @staticmethod
    def _memory_values(
        document_status: str,
        state: DocumentMemoryState | None,
    ) -> dict[str, Any]:
        if document_status in {"pending", "processing"}:
            memory_status = "pending"
        elif state is None:
            memory_status = "missing"
        else:
            memory_status = state.status
        return {
            "memory_status": memory_status,
            "memory_error_msg": state.error_msg if state else None,
            "memory_count": state.memory_count if state else 0,
            "memory_link_count": state.link_count if state else 0,
        }
