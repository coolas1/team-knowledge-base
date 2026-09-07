"""Small façade exposing only retain, recall, and reflect."""

from __future__ import annotations

from .config import HindsightOptions
from .protocols import HindsightProviders, MemoryRepository
from .recall import RecallEngine
from .reflect import ReflectEngine
from .retain import RetainEngine
from .types import RecallResult, ReflectResult, RetainInput, RetainResult


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
        retain_input: RetainInput | None = None,
        *,
        document_id: str | None = None,
        title: str | None = None,
        content: str | None = None,
        file_type: str | None = None,
        source_type: str = "upload",
        context: str | None = None,
        tags: tuple[str, ...] = (),
        metadata: dict | None = None,
    ) -> RetainResult:
        if retain_input is None:
            if None in (document_id, title, content, file_type):
                raise TypeError(
                    "document_id, title, content, and file_type are required"
                )
            assert document_id is not None
            assert title is not None
            assert content is not None
            assert file_type is not None
            retain_input = RetainInput(
                document_id=document_id,
                title=title,
                content=content,
                file_type=file_type,
                source_type=source_type,
                context=context,
                tags=tags,
                metadata=dict(metadata or {}),
            )
        return await self._retain.retain(retain_input)

    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        source_type: str | None = None,
    ) -> RecallResult:
        return await self._recall.recall(
            query,
            mode=mode,
            top_k=top_k,
            source_type=source_type,
        )

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
    ) -> ReflectResult:
        return await self._reflect.reflect(query, mode=mode, top_k=top_k)
