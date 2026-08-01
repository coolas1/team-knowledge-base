"""Ports implemented by existing project infrastructure in later batches."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .types import RecallCandidate, ReflectionContext, RetainPlan


class HindsightProviders(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def json(
        self, system: str, user: str, *, timeout: float = 600
    ) -> dict[str, Any]: ...

    async def text(self, system: str, user: str, *, timeout: float = 600) -> str: ...


class MemoryRepository(Protocol):
    """Storage/retrieval operations; no document or file management lives here."""

    async def replace_document(self, plan: RetainPlan) -> None: ...

    async def semantic_neighbors(
        self,
        embedding: list[float],
        *,
        exclude_document_id: str,
        limit: int,
    ) -> list[tuple[str, float]]: ...

    async def semantic_search(
        self, embedding: list[float], limit: int
    ) -> list[RecallCandidate]: ...

    async def keyword_search(self, query: str, limit: int) -> list[RecallCandidate]: ...

    async def graph_search(
        self, entities: list[str], limit: int
    ) -> list[RecallCandidate]: ...

    async def temporal_search(
        self,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[RecallCandidate]: ...

    async def entity_states(self, memory_ids: list[str]) -> dict[str, Any]: ...

    async def reflection_context(
        self, query: str, query_embedding: list[float]
    ) -> ReflectionContext: ...
