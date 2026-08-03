"""Compatibility adapter that enriches the original document API with memory state."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.engine.interface import (
    DocumentRef,
    GraphData,
    IngestSource,
    KnowledgeBase,
    RecallRequest,
    RecallResult,
)

from .repository import PostgresMemoryRepository
from .types import DocumentMemoryState

logger = logging.getLogger(__name__)


class DocumentStateReader(Protocol):
    async def document_state(self, document_id: str) -> DocumentMemoryState | None: ...

    async def document_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]: ...


class HindsightKnowledgeBaseAdapter:
    """Keep the original ``KnowledgeBase`` API while adding Hindsight state.

    Search takeover remains in the recall compatibility adapter. Graph methods
    deliberately delegate to GraphRAG so the existing entity graph stays small.
    """

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        state_reader: DocumentStateReader,
    ) -> None:
        self._knowledge_base = knowledge_base
        self._state_reader = state_reader
        self.capabilities = knowledge_base.capabilities

    async def ingest(self, source: IngestSource) -> DocumentRef:
        ref = await self._knowledge_base.ingest(source)
        self._enrich_ref(ref, None)
        return ref

    async def reingest(self, doc_id: str) -> DocumentRef:
        ref = await self._knowledge_base.reingest(doc_id)
        state = await self._safe_state(doc_id)
        self._enrich_ref(ref, state)
        return ref

    async def remove(self, doc_id: str) -> None:
        await self._knowledge_base.remove(doc_id)

    async def recall(self, request: RecallRequest) -> RecallResult:
        return await self._knowledge_base.recall(request)

    async def get_graph(self, entity: str | None = None) -> GraphData:
        return await self._knowledge_base.get_graph(entity)

    async def get_neighbors(self, entity: str) -> GraphData:
        return await self._knowledge_base.get_neighbors(entity)

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        result = await self._knowledge_base.list_documents(
            page, page_size, file_type, status
        )
        items = result.get("items", [])
        document_ids = []
        for item in items:
            document_id = (
                item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            )
            if document_id:
                document_ids.append(str(document_id))
        states = await self._safe_states(document_ids)
        for item in items:
            if isinstance(item, dict):
                document_id = str(item.get("id", ""))
                self._enrich_dict(item, states.get(document_id))
            elif isinstance(item, DocumentRef):
                self._enrich_ref(item, states.get(item.id))
        return result

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        document = await self._knowledge_base.get_document(doc_id)
        if document is None:
            return None
        self._enrich_dict(document, await self._safe_state(doc_id))
        return document

    async def _safe_state(self, document_id: str) -> DocumentMemoryState | None:
        try:
            return await self._state_reader.document_state(document_id)
        except Exception:
            logger.exception("Failed to read Hindsight state for %s", document_id)
            return DocumentMemoryState(document_id=document_id, status="unavailable")

    async def _safe_states(
        self, document_ids: list[str]
    ) -> dict[str, DocumentMemoryState]:
        try:
            return await self._state_reader.document_states(document_ids)
        except Exception:
            logger.exception("Failed to read Hindsight states")
            return {
                document_id: DocumentMemoryState(
                    document_id=document_id,
                    status="unavailable",
                )
                for document_id in document_ids
            }

    @classmethod
    def _enrich_ref(
        cls,
        ref: DocumentRef,
        state: DocumentMemoryState | None,
    ) -> None:
        values = cls._memory_values(ref.status, state)
        ref.memory_status = values["memory_status"]
        ref.memory_error_msg = values["memory_error_msg"]
        ref.memory_count = values["memory_count"]
        ref.memory_link_count = values["memory_link_count"]

    @classmethod
    def _enrich_dict(
        cls,
        document: dict[str, Any],
        state: DocumentMemoryState | None,
    ) -> None:
        document.update(cls._memory_values(str(document.get("status", "")), state))

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


def build_knowledge_base_adapter(
    knowledge_base: KnowledgeBase,
) -> HindsightKnowledgeBaseAdapter:
    return HindsightKnowledgeBaseAdapter(knowledge_base, PostgresMemoryRepository())
