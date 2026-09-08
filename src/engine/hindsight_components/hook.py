"""Failure-isolated GraphRAG lifecycle hook for Hindsight retain."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from src.engine.hindsight_components.providers import ProjectHindsightProviders
from src.engine.hindsight_components.protocols import MemoryRepository
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.service import HindsightService
from src.engine.hindsight_components.types import RetainInput, RetentionRevisionConflict
from src.engine.scope import MemoryScope

logger = logging.getLogger(__name__)


class RetainService(Protocol):
    async def retain(self, retain_input: RetainInput): ...


class RetainRepository(Protocol):
    async def set_document_state(
        self,
        document_id: str,
        status: str,
        *,
        error_msg: str | None = None,
    ) -> None: ...

    async def delete_document(self, document_id: str) -> None: ...


class HindsightRetainHook:
    def __init__(
        self,
        service: RetainService,
        repository: RetainRepository,
        *,
        max_concurrent: int = 1,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be greater than zero")
        self._service = service
        self._repository = repository
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._write_tags: tuple[str, ...] = ()

    def with_scope(self, scope: MemoryScope, *, write_tags: tuple[str, ...] = ()):
        hook = HindsightRetainHook(
            self._service.with_scope(scope), self._repository.with_scope(scope)
        )
        hook._semaphore = self._semaphore
        hook._write_tags = write_tags
        return hook

    async def after_indexed(
        self,
        *,
        document_id: str,
        title: str,
        content: str,
        file_type: str,
    ) -> None:
        await self._repository.set_document_state(document_id, "retaining")
        try:
            async with self._semaphore:
                await self._service.retain(
                    RetainInput(
                        document_id=document_id,
                        title=title,
                        content=content,
                        file_type=file_type,
                        source_type="graphrag-pipeline",
                        tags=self._write_tags,
                    )
                )
        except RetentionRevisionConflict:
            logger.info("Retain superseded by newer revision for %s", document_id)
        except Exception as error:
            logger.exception("Hindsight retain failed for %s", document_id)
            await self._repository.set_document_state(
                document_id, "failed", error_msg=str(error)
            )

    async def before_remove(self, document_id: str) -> None:
        try:
            await self._repository.delete_document(document_id)
        except Exception:
            # The Document FK cascade remains the final cleanup guarantee.
            logger.exception("Hindsight cleanup failed for %s", document_id)


def build_retain_hook(
    *,
    max_concurrent: int = 1,
    repository: MemoryRepository | None = None,
) -> HindsightRetainHook:
    repository = repository or PostgresMemoryRepository()
    service = HindsightService(repository, ProjectHindsightProviders())
    return HindsightRetainHook(service, repository, max_concurrent=max_concurrent)
