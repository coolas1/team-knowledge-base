"""Unified recall/reflect query adapter for the public engine contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Protocol

from src.engine.interface import (
    DirectiveDefinition as PublicDirectiveDefinition,
    DirectiveRecord,
    KnowledgeQueryRequest,
    KnowledgeQueryResult,
    KnowledgeSource,
    MemoryExpansionRequest,
    MemoryExpansionResult,
    MentalModelDefinition as PublicMentalModelDefinition,
    MentalModelRecord,
)
from src.engine.hindsight_components.directives import DirectiveDefinition
from src.engine.hindsight_components.mental_models import MentalModelDefinition

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.providers import ProjectHindsightProviders
from src.engine.hindsight_components.protocols import MemoryRepository
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.service import HindsightService
from src.engine.hindsight_components.types import (
    RecallCandidate,
    RecallFilter,
    RecallResult,
    ReflectResult,
)
from src.engine.scope import request_tag_filter


class CoreQueryService(Protocol):
    async def expand_memory(self, memory_id: str, **kwargs): ...

    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult: ...

    async def reflect(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
    ) -> ReflectResult: ...


class HindsightQueryService:
    """Choose raw recall or grounded reflection without exposing both APIs."""

    def __init__(self, core: CoreQueryService) -> None:
        self._core = core

    def with_scope(self, scope):
        return HindsightQueryService(self._core.with_scope(scope))

    @staticmethod
    def _model_record(value) -> MentalModelRecord:
        return MentalModelRecord(
            **{
                field: getattr(value, field)
                for field in MentalModelRecord.__dataclass_fields__
            }
        )

    @staticmethod
    def _model_definition(value: PublicMentalModelDefinition) -> MentalModelDefinition:
        return MentalModelDefinition(
            **{
                field: getattr(value, field)
                for field in MentalModelDefinition.__dataclass_fields__
            }
        )

    async def create_mental_model(self, definition):
        value = await self._core.create_mental_model(self._model_definition(definition))
        return self._model_record(value)

    async def get_mental_model(self, model_id: str):
        value = await self._core.get_mental_model(model_id)
        return self._model_record(value) if value else None

    async def list_mental_models(self):
        return [
            self._model_record(value) for value in await self._core.list_mental_models()
        ]

    async def update_mental_model(self, model_id: str, definition):
        value = await self._core.update_mental_model(
            model_id, self._model_definition(definition)
        )
        return self._model_record(value)

    async def delete_mental_model(self, model_id: str) -> bool:
        return await self._core.delete_mental_model(model_id)

    async def refresh_mental_model(self, model_id: str) -> bool:
        return await self._core.refresh_mental_model(model_id)

    @staticmethod
    def _directive_definition(value: PublicDirectiveDefinition) -> DirectiveDefinition:
        return DirectiveDefinition(
            **{
                field: getattr(value, field)
                for field in DirectiveDefinition.__dataclass_fields__
            }
        )

    @staticmethod
    def _directive_record(value) -> DirectiveRecord:
        return DirectiveRecord(
            **{
                field: getattr(value, field)
                for field in DirectiveRecord.__dataclass_fields__
            }
        )

    async def create_directive(self, definition):
        value = await self._core.create_directive(
            self._directive_definition(definition)
        )
        return self._directive_record(value)

    async def list_directives(self):
        return [
            self._directive_record(value)
            for value in await self._core.list_directives()
        ]

    async def update_directive(self, directive_id: str, definition):
        value = await self._core.update_directive(
            directive_id, self._directive_definition(definition)
        )
        return self._directive_record(value)

    async def delete_directive(self, directive_id: str) -> bool:
        return await self._core.delete_directive(directive_id)

    async def list_memory_operations(self, **filters):
        return await self._core.list_memory_operations(**filters)

    async def get_memory_operation(self, operation_id: str):
        return await self._core.get_memory_operation(operation_id)

    async def retry_memory_operation(self, operation_id: str) -> int:
        return await self._core.retry_memory_operation(operation_id)

    async def cancel_memory_operation(self, operation_id: str) -> int:
        return await self._core.cancel_memory_operation(operation_id)

    async def list_memory_facts(self, *, limit: int = 100):
        return await self._core.list_memory_facts(limit=limit)

    async def get_observation_detail(self, observation_id: str):
        return await self._core.get_observation_detail(observation_id)

    async def correct_memory_entity(self, source_entity_id, memory_ids, **kwargs):
        return await self._core.correct_entity(source_entity_id, memory_ids, **kwargs)

    async def get_memory_policy(self):
        return await self._core.get_memory_policy()

    async def update_memory_policy(self, policy, *, expected_version: int):
        return await self._core.update_memory_policy(
            policy, expected_version=expected_version
        )

    async def expand_memory(
        self, request: MemoryExpansionRequest
    ) -> MemoryExpansionResult | None:
        expanded = await self._core.expand_memory(
            request.memory_id,
            include=request.include,
            max_tokens=request.max_tokens,
        )
        if expanded is None:
            return None
        return MemoryExpansionResult(
            memory=expanded.memory,
            chunk=expanded.chunk,
            document=expanded.document,
            source_facts=expanded.source_facts,
            token_count=expanded.token_count,
            truncated=expanded.truncated,
        )

    async def query(self, request: KnowledgeQueryRequest) -> KnowledgeQueryResult:
        self._validate(request)
        strategy = self._resolve_strategy(request)
        if strategy == "recall":
            filters = self._filters(request)
            recalled = await self._core.recall(
                request.query,
                mode=request.mode,
                top_k=request.top_k,
                **({"filters": filters} if filters is not None else {}),
            )
            grouped: defaultdict[str, list[dict]] = defaultdict(list)
            for item in recalled.results:
                grouped[item.memory_type].append(item.as_evidence())
            return KnowledgeQueryResult(
                strategy_used="recall",
                sources=[
                    self._source_from_candidate(item, recalled)
                    for item in recalled.results
                ],
                related_entities=self._related_entities(recalled.entities),
                based_on=dict(grouped),
                trace=dict(recalled.trace),
            )

        filters = self._filters(request)
        reflected = await self._core.reflect(
            request.query,
            mode=request.mode,
            top_k=request.top_k,
            **({"filters": filters} if filters is not None else {}),
        )
        return KnowledgeQueryResult(
            strategy_used="reflect",
            answer=reflected.text,
            sources=self._sources_from_reflection(reflected),
            based_on=reflected.based_on,
            trace={"tool_trace": list(reflected.tool_trace)},
        )

    @staticmethod
    def _validate(request: KnowledgeQueryRequest) -> None:
        if not request.query.strip():
            raise ValueError("query cannot be empty")
        if request.strategy not in {"auto", "recall", "reflect"}:
            raise ValueError(f"unsupported query strategy: {request.strategy}")
        if request.mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {request.mode}")
        if request.top_k < 1:
            raise ValueError("top_k must be greater than zero")

    @staticmethod
    def _resolve_strategy(request: KnowledgeQueryRequest) -> str:
        if request.strategy != "auto":
            return request.strategy
        return "reflect" if request.needs_answer else "recall"

    @staticmethod
    def _filters(request: KnowledgeQueryRequest) -> RecallFilter | None:
        extended = any(
            (
                request.memory_types,
                request.source_types,
                request.tags,
                request.tags_match != "any",
                request.reference_time,
                request.min_scores,
                request.prefer_observations,
                request.include != ("chunks", "entities"),
                request.include_stale,
                request.timeout_seconds,
                request.max_tokens,
                request.max_candidates,
            )
        )
        if not extended:
            return None
        return RecallFilter(
            memory_types=request.memory_types,
            source_types=request.source_types,
            tags=request_tag_filter(request.tags, request.tags_match),
            reference_time=request.reference_time,
            min_scores=request.min_scores,
            prefer_observations=request.prefer_observations,
            include=request.include,
            include_stale=request.include_stale,
            timeout_seconds=request.timeout_seconds,
            max_tokens=request.max_tokens,
            max_candidates=request.max_candidates,
        )

    @staticmethod
    def _source_from_candidate(
        item: RecallCandidate, recalled: RecallResult
    ) -> KnowledgeSource:
        chunk_id = f"{item.document_id}_{item.chunk_index}"
        chunk = recalled.chunks.get(chunk_id, {})
        return KnowledgeSource(
            memory_id=item.id,
            memory_type=item.memory_type,
            doc_id=item.document_id,
            title=item.title,
            chunk_text=str(chunk.get("text") or item.text),
            score=item.final_score,
            metadata={
                **dict(item.metadata),
                "source_type": item.source_type,
                "mentioned_at": item.mentioned_at,
                "updated_at": item.updated_at,
                "occurred_start": item.occurred_start,
                "occurred_end": item.occurred_end,
                "freshness": item.freshness,
                "stale_reason": item.stale_reason,
                **(
                    {"document": recalled.documents[item.document_id]}
                    if item.document_id in recalled.documents
                    else {}
                ),
                **({"session_id": item.session_id} if item.session_id else {}),
                **({"turn_id": item.turn_id} if item.turn_id else {}),
                "scores": {
                    "final": item.final_score,
                    "reranker": item.reranker_score,
                    "semantic": item.semantic_score,
                    "keyword": item.keyword_score,
                    "graph": item.graph_score,
                    "temporal": item.temporal_score,
                },
            },
        )

    @staticmethod
    def _related_entities(entities: Mapping[str, object]) -> list[dict]:
        output = []
        for name, state in entities.items():
            if isinstance(state, Mapping):
                output.append({"name": name, **dict(state)})
            else:
                output.append({"name": name, "state": state})
        return output

    @staticmethod
    def _sources_from_reflection(reflected: ReflectResult) -> list[KnowledgeSource]:
        sources: list[KnowledgeSource] = []
        seen: set[str] = set()
        actual_ids = {
            str(item["id"])
            for item in reflected.actual_citations
            if item.get("type") == "memory" and item.get("id")
        }
        validated_citations = "actual_citations" in reflected.based_on
        for memory_type, items in reflected.based_on.items():
            if memory_type in {
                "directives",
                "mental_models",
                "retrieved_mental_models",
                "actual_citations",
            }:
                continue
            for item in items:
                memory_id = str(item.get("id", ""))
                if (
                    not memory_id
                    or memory_id in seen
                    or (validated_citations and memory_id not in actual_ids)
                ):
                    continue
                seen.add(memory_id)
                metadata = item.get("metadata", {})
                metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
                metadata.setdefault("source_type", item.get("source_type", "upload"))
                if item.get("session_id"):
                    metadata.setdefault("session_id", item["session_id"])
                if item.get("turn_id"):
                    metadata.setdefault("turn_id", item["turn_id"])
                scores = item.get("scores", {})
                scores = scores if isinstance(scores, Mapping) else {}
                sources.append(
                    KnowledgeSource(
                        memory_id=memory_id,
                        memory_type=str(item.get("type") or memory_type),
                        doc_id=str(item.get("document_id", "")),
                        title=str(metadata.get("title", "")),
                        chunk_text=str(item.get("text", "")),
                        score=float(scores.get("final") or 0.0),
                        metadata={**metadata, "scores": dict(scores)},
                    )
                )
        return sources


def build_query_service(
    *,
    repository: MemoryRepository | None = None,
) -> HindsightQueryService:
    from config.settings import settings
    from config.schema import load_config
    import os

    repository = repository or PostgresMemoryRepository(
        keyword_index_enabled=settings.hindsight_keyword_index_enabled,
        keyword_candidate_limit=settings.hindsight_keyword_candidate_limit,
    )
    app_config = load_config(os.getenv("APP_CONFIG", "config/app.yaml"))
    memory_config = app_config.engine.memory
    options = HindsightOptions(
        file_summary_enabled=app_config.engine.ingest.vector_only,
        adaptive_reflect_enabled=memory_config.features.adaptive_reflect,
        fact_cache_capacity=memory_config.fact_cache_capacity,
        fact_cache_ttl_seconds=memory_config.fact_cache_ttl_seconds,
        fact_context_limit=memory_config.fact_context_limit,
        fact_context_max_tokens=memory_config.fact_context_max_tokens,
        recall_max_results=memory_config.recall_max_results,
        recall_max_candidates=memory_config.recall_max_candidates,
        recall_max_tokens=memory_config.recall_max_tokens,
        reflect_max_iterations=memory_config.reflect_max_iterations,
        reflect_max_tokens=memory_config.reflect_max_tokens,
        reflect_total_timeout_seconds=memory_config.reflect_total_timeout_seconds,
        recall_min_semantic=settings.hindsight_recall_min_semantic,
        recall_min_score=settings.hindsight_recall_min_score,
        conversation_recall_min_semantic=(
            settings.hindsight_conversation_recall_min_semantic
        ),
        rerank_semantic_margin=settings.hindsight_rerank_semantic_margin,
        deep_total_timeout_seconds=settings.hindsight_deep_total_timeout_seconds,
        query_analysis_timeout_seconds=settings.hindsight_query_analysis_timeout_seconds,
        query_embedding_timeout_seconds=settings.hindsight_query_embedding_timeout_seconds,
        retrieval_arm_timeout_seconds=settings.hindsight_retrieval_arm_timeout_seconds,
        rerank_timeout_seconds=settings.hindsight_rerank_timeout_seconds,
        rerank_candidate_limit=settings.hindsight_rerank_candidate_limit,
        rerank_text_limit_chars=settings.hindsight_rerank_text_limit_chars,
        rerank_total_chars=settings.hindsight_rerank_total_chars,
        keyword_candidate_limit=settings.hindsight_keyword_candidate_limit,
    )
    core = HindsightService(repository, ProjectHindsightProviders(), options)
    return HindsightQueryService(core)
