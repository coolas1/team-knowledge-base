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
    MemoryExpansion,
    RecallFilter,
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
        mental_models=None,
    ) -> None:
        if options is None:
            import os
            from config.schema import load_config

            features = load_config(
                os.getenv("APP_CONFIG", "config/app.yaml")
            ).engine.memory.features
            options = HindsightOptions(
                entity_resolution_enabled=features.entity_resolution,
                consolidation_enabled=features.consolidation,
            )
        self.options = options
        self._repository = repository
        self._providers = providers
        self._retain = RetainEngine(repository, providers, self.options)
        self._recall = RecallEngine(repository, providers, self.options)
        self._reflect = ReflectEngine(self._recall, repository, providers, self.options)
        if mental_models is None:
            from .mental_models import PostgresMentalModelRepository

            mental_models = PostgresMentalModelRepository(
                getattr(repository, "_session_factory", None),
                scope=getattr(repository, "scope", None),
            )
        self._mental_models = mental_models

    def with_scope(self, scope):
        return HindsightService(
            self._repository.with_scope(scope),
            self._providers,
            self.options,
            mental_models=self._mental_models.with_scope(scope),
        )

    def with_lease(self, document_id: str, lease_token: str):
        return HindsightService(
            self._repository.with_lease(document_id, lease_token),
            self._providers,
            self.options,
            mental_models=self._mental_models,
        )

    async def create_mental_model(self, definition):
        return await self._mental_models.create(definition)

    async def get_mental_model(self, model_id: str):
        return await self._mental_models.get(model_id)

    async def list_mental_models(self):
        return await self._mental_models.list()

    async def update_mental_model(self, model_id: str, definition):
        return await self._mental_models.update(model_id, definition)

    async def delete_mental_model(self, model_id: str) -> bool:
        return await self._mental_models.delete(model_id)

    async def refresh_mental_model(self, model_id: str) -> bool:
        return await self._mental_models.enqueue(model_id)

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
        update_mode: str = "replace",
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
                update_mode=update_mode,
            )
        return await self._retain.retain(retain_input)

    async def reprocess_document(
        self, document_id: str, *, stage: str = "extract"
    ) -> RetainResult:
        if stage != "extract":
            raise ValueError("unsupported retention stage")
        from dataclasses import replace

        return await self._retain.retain(
            replace(
                await self._repository.retention_input(document_id),
                force_extraction=True,
            ),
            replay_snapshot=True,
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
        filters: RecallFilter | None = None,
    ) -> RecallResult:
        return await self._recall.recall(
            query,
            mode=mode,
            top_k=top_k,
            source_type=source_type,
            search_id=search_id,
            filters=filters,
        )

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        filters: RecallFilter | None = None,
    ) -> ReflectResult:
        return await self._reflect.reflect(
            query, mode=mode, top_k=top_k, filters=filters
        )

    async def expand_memory(
        self,
        memory_id: str,
        *,
        include: tuple[str, ...] = ("chunk", "document", "source_facts"),
        max_tokens: int | None = None,
    ) -> MemoryExpansion | None:
        if set(include) - {"chunk", "document", "source_facts"}:
            raise ValueError("unsupported memory expansion include")
        token_limit = min(
            max_tokens or self.options.recall_max_tokens,
            self.options.recall_max_tokens,
        )
        if token_limit < 1:
            raise ValueError("max_tokens must be positive")
        record = await self._repository.expand_memory_record(memory_id)
        if record is None:
            return None
        remaining_chars = token_limit * 4
        truncated = False

        def bounded(value):
            nonlocal remaining_chars, truncated
            if value is None:
                return None
            result = dict(value)
            text = str(result.get("text", ""))
            if len(text) > remaining_chars:
                text = text[:remaining_chars]
                truncated = True
            result["text"] = text
            remaining_chars -= len(text)
            return result

        chunk = bounded(record["chunk"]) if "chunk" in include else None
        document = bounded(record["document"]) if "document" in include else None
        facts = []
        if "source_facts" in include:
            for fact in record["source_facts"]:
                bounded_fact = bounded(fact)
                if bounded_fact is not None and bounded_fact.get("text"):
                    facts.append(bounded_fact)
                if remaining_chars <= 0:
                    truncated = True
                    break
        memory = dict(record["memory"])
        memory.update(
            freshness=record["freshness"],
            stale_reason=record["stale_reason"],
            updated_at=record["updated_at"],
        )
        return MemoryExpansion(
            memory=memory,
            chunk=chunk,
            document=document,
            source_facts=tuple(facts),
            token_count=(token_limit * 4 - remaining_chars + 3) // 4,
            truncated=truncated,
        )
