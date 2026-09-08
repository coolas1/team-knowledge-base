"""Small façade exposing only retain, recall, and reflect."""

from __future__ import annotations

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.protocols import (
    HindsightProviders,
    MemoryRepository,
)
from src.engine.hindsight_components.recall import RecallEngine
from src.engine.hindsight_components.reflect import ReflectEngine
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.types import (
    RecallResult,
    ReflectResult,
    RetainInput,
    RetainResult,
)


class HindsightService:
    def __init__(
        self,
        repository: MemoryRepository,
        providers: HindsightProviders,
        options: HindsightOptions | None = None,
    ) -> None:
        if options is None:
            import os
            from config.schema import load_config

            features = load_config(
                os.getenv("APP_CONFIG", "config/app.yaml")
            ).engine.memory.features
            options = HindsightOptions(
                entity_resolution_enabled=features.entity_resolution
            )
        self.options = options
        self._repository = repository
        self._providers = providers
        self._retain = RetainEngine(repository, providers, self.options)
        self._recall = RecallEngine(repository, providers, self.options)
        self._reflect = ReflectEngine(self._recall, repository, providers, self.options)

    def with_scope(self, scope):
        return HindsightService(
            self._repository.with_scope(scope), self._providers, self.options
        )

    def with_lease(self, document_id: str, lease_token: str):
        return HindsightService(
            self._repository.with_lease(document_id, lease_token),
            self._providers,
            self.options,
        )

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
        agent_name: str | None = None,
        speakers: dict | None = None,
        source_timestamp=None,
        reference_timezone: str = "UTC",
        policy_version: int = 1,
        expected_revision: int | None = None,
        request_id: str | None = None,
        force_extraction: bool = False,
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
                agent_name=agent_name,
                speakers=dict(speakers or {}),
                source_timestamp=source_timestamp,
                reference_timezone=reference_timezone,
                policy_version=policy_version,
                expected_revision=expected_revision,
                request_id=request_id,
                force_extraction=force_extraction,
            )
        return await self._retain.retain(retain_input)

    async def reprocess_document(
        self, document_id: str, *, stage: str = "extract"
    ) -> RetainResult:
        if stage != "extract":
            raise ValueError("unsupported retention stage")
        from dataclasses import replace

        return await self.retain(
            replace(
                await self._repository.retention_input(document_id),
                force_extraction=True,
            )
        )

    async def retention_revision(self, document_id: str) -> int:
        return await self._repository.retention_revision(document_id)

    async def correct_entity(
        self,
        source_entity_id: str,
        memory_ids: list[str],
        *,
        target_entity_id: str | None = None,
        reason: str,
    ):
        from .entity_correction import correct_entity

        return await correct_entity(
            self._repository,
            source_entity_id,
            memory_ids,
            target_entity_id=target_entity_id,
            reason=reason,
        )

    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        source_type: str | None = None,
        search_id: str | None = None,
    ) -> RecallResult:
        return await self._recall.recall(
            query,
            mode=mode,
            top_k=top_k,
            source_type=source_type,
            search_id=search_id,
        )

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
    ) -> ReflectResult:
        return await self._reflect.reflect(query, mode=mode, top_k=top_k)
