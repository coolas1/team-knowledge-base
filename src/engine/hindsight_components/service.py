"""Small façade exposing only retain, recall, and reflect."""

from __future__ import annotations

from .config import HindsightOptions
from .protocols import HindsightProviders, MemoryRepository
from .recall import RecallEngine
from .reflect import ReflectEngine
from .retain import RetainEngine
from .types import RecallResult, ReflectResult, RetainResult


class HindsightService:
    def __init__(
        self,
        repository: MemoryRepository,
        providers: HindsightProviders,
        options: HindsightOptions | None = None,
    ) -> None:
        self.options = options or HindsightOptions()
        self._retain = RetainEngine(repository, providers, self.options)
        self._recall = RecallEngine(repository, providers, self.options)
        self._reflect = ReflectEngine(self._recall, repository, providers, self.options)

    async def retain(
        self,
        *,
        document_id: str,
        title: str,
        content: str,
        file_type: str,
        source_type: str = "upload",
    ) -> RetainResult:
        return await self._retain.retain(
            document_id=document_id,
            title=title,
            content=content,
            file_type=file_type,
            source_type=source_type,
        )

    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult:
        return await self._recall.recall(query, mode=mode, top_k=top_k)

    async def reflect(self, query: str) -> ReflectResult:
        return await self._reflect.reflect(query)
