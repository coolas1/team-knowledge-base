"""Unified recall/reflect query adapter for the public engine contract."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
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
from src.engine.hindsight_components.deadlines import DeadlineBudget
from src.engine.hindsight_components.errors import DeepSearchTimeoutError
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

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        core: CoreQueryService,
        *,
        mixed_source_search_enabled: bool = True,
        knowledge_memory_context_enabled: bool = False,
        knowledge_memory_context_limit: int = 2,
    ) -> None:
        self._core = core
        self._mixed_source_search_enabled = mixed_source_search_enabled
        self._knowledge_memory_context_enabled = knowledge_memory_context_enabled
        self._knowledge_memory_context_limit = max(0, knowledge_memory_context_limit)

    def with_scope(self, scope):
        return HindsightQueryService(
            self._core.with_scope(scope),
            mixed_source_search_enabled=self._mixed_source_search_enabled,
            knowledge_memory_context_enabled=self._knowledge_memory_context_enabled,
            knowledge_memory_context_limit=self._knowledge_memory_context_limit,
        )

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
        configured_total = float(
            getattr(
                getattr(self._core, "options", None),
                "deep_total_timeout_seconds",
                45.0,
            )
        )
        total = min(configured_total, request.timeout_seconds or configured_total)
        budget = DeadlineBudget(total)
        try:
            async with asyncio.timeout(budget.remaining()):
                return await self._query_with_budget(request, budget)
        except TimeoutError as error:
            search_id = request.correlation_id or str(uuid.uuid4())
            raise DeepSearchTimeoutError(
                search_id,
                {
                    "search_id": search_id,
                    "outcome": "deep_search_timeout",
                    "elapsed_ms": budget.elapsed_ms(),
                    "total_timeout_seconds": total,
                    "category": "outer_query_deadline",
                },
            ) from error

    async def _query_with_budget(
        self, request: KnowledgeQueryRequest, budget: DeadlineBudget
    ) -> KnowledgeQueryResult:
        self._validate(request)
        requested_route = request.route
        route_fallback = None
        if request.route == "mixed" and not self._mixed_source_search_enabled:
            request = replace(request, route="knowledge", source_types=("upload",))
            route_fallback = "mixed_source_search_disabled"
        strategy = self._resolve_strategy(request)
        if strategy == "recall":
            recalled = await self._recall_for_route(request, budget)
            recalled = self._enforce_route_candidates(recalled, request.route)
            recalled = self._collapse_and_cap(recalled)
            kept, texts, evidence_trace = self._bound_evidence(recalled)
            # based_on duplicates the per-source evidence; it is opt-in and
            # compact (ids and scores, no repeated evidence text).
            include_based_on = "based_on" in request.include
            grouped: defaultdict[str, list[dict]] = defaultdict(list)
            for item in kept if include_based_on else ():
                grouped[item.memory_type].append(
                    {
                        "id": item.id,
                        "type": item.memory_type,
                        "document_id": item.document_id,
                        "freshness": item.freshness,
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
                    }
                )
            trace = dict(recalled.trace)
            trace.update(evidence_trace)
            sources = [
                self._source_from_candidate(item, texts[item.id]) for item in kept
            ]
            document_evidence = [
                source for source in sources if source.authority == "document"
            ]
            conversation_context = [
                source for source in sources if source.authority == "conversation"
            ]
            logger.info(
                "hindsight.knowledge_query.complete",
                extra={
                    "query_route": request.route,
                    "requested_route": requested_route,
                    "document_result_count": len(document_evidence),
                    "conversation_result_count": len(conversation_context),
                    "source_pool_counts": {
                        "upload": len(document_evidence),
                        "conversation": len(conversation_context),
                    },
                    "conversation_leakage_count": sum(
                        source.authority == "conversation"
                        for source in document_evidence
                    ),
                    "duplicate_collapsed": trace.get("duplicate_collapsed", 0),
                    "document_coverage": trace.get("document_coverage", 0),
                    "fallback": route_fallback or trace.get("fallback"),
                },
            )
            return KnowledgeQueryResult(
                strategy_used="recall",
                sources=document_evidence,
                related_entities=self._related_entities(recalled.entities),
                based_on=dict(grouped),
                trace={
                    **trace,
                    "route": request.route,
                    "requested_route": requested_route,
                    **({"fallback": route_fallback} if route_fallback else {}),
                },
                route_used=request.route,
                document_evidence=document_evidence,
                conversation_context=conversation_context,
            )

        filters = self._filters(request)
        reflect_kwargs = {"mode": request.mode, "top_k": request.top_k}
        if filters is not None and "filters" in inspect.signature(
            self._core.reflect
        ).parameters:
            reflect_kwargs["filters"] = filters
        reflected = await self._core.reflect(request.query, **reflect_kwargs)
        sources = self._sources_from_reflection(reflected)
        document_evidence = [
            source for source in sources if source.authority == "document"
        ]
        conversation_context = [
            source for source in sources if source.authority == "conversation"
        ]
        return KnowledgeQueryResult(
            strategy_used="reflect",
            answer=reflected.text,
            sources=document_evidence,
            based_on=self._compact_based_on(reflected.based_on),
            trace={
                "tool_trace": list(reflected.tool_trace),
                "route": request.route,
                "requested_route": requested_route,
                **({"fallback": route_fallback} if route_fallback else {}),
            },
            route_used=request.route,
            document_evidence=document_evidence,
            conversation_context=conversation_context,
        )

    async def _recall_for_route(
        self, request: KnowledgeQueryRequest, budget: DeadlineBudget
    ) -> RecallResult:
        if request.route == "conversation":
            return await self._recall_source_pool(
                request, source_type="conversation", limit=request.top_k, budget=budget
            )

        if request.route == "knowledge":
            document_call = self._recall_source_pool(
                request, source_type="upload", limit=request.top_k, budget=budget
            )
            # Explicit source filters are authoritative. Auxiliary memories are
            # only added for the ordinary/default knowledge contract.
            include_context = (
                self._knowledge_memory_context_enabled
                and self._knowledge_memory_context_limit > 0
                and not request.source_types
            )
            if not include_context:
                return await document_call
            conversation_limit = self._knowledge_memory_context_limit
            document_result, conversation_result = await asyncio.gather(
                document_call,
                self._recall_source_pool(
                    request,
                    source_type="conversation",
                    limit=conversation_limit,
                    budget=budget,
                ),
            )
            return self._merge_source_pools(
                document_result,
                conversation_result,
                route="knowledge",
                document_quota=request.top_k,
                conversation_quota=conversation_limit,
                auxiliary=True,
            )

        # Mixed retrieval is deliberately two independent source-local calls.
        # Raw lexical scores from short conversations and document passages
        # never compete in one BM25 population. Documents receive the
        # authoritative majority quota and remain first in compatibility output.
        document_limit = max(1, (request.top_k * 4 + 4) // 5)
        document_limit = min(document_limit, request.top_k)
        conversation_limit = request.top_k - document_limit
        document_call = self._recall_source_pool(
            request, source_type="upload", limit=document_limit, budget=budget
        )
        if conversation_limit:
            document_result, conversation_result = await asyncio.gather(
                document_call,
                self._recall_source_pool(
                    request,
                    source_type="conversation",
                    limit=conversation_limit,
                    budget=budget,
                ),
            )
        else:
            document_result = await document_call
            conversation_result = RecallResult(
                results=[], chunks={}, entities={}, trace={"skipped": "zero_quota"}
            )
        return self._merge_source_pools(
            document_result,
            conversation_result,
            route="mixed",
            document_quota=document_limit,
            conversation_quota=conversation_limit,
            auxiliary=False,
        )

    async def _recall_source_pool(
        self,
        request: KnowledgeQueryRequest,
        *,
        source_type: str,
        limit: int,
        budget: DeadlineBudget,
    ) -> RecallResult:
        pool_route = "conversation" if source_type == "conversation" else "knowledge"
        pool_request = replace(
            request,
            route=pool_route,
            source_types=(source_type,),
            top_k=limit,
        )
        kwargs = {"mode": request.mode, "top_k": limit}
        if "filters" in inspect.signature(self._core.recall).parameters:
            kwargs["filters"] = self._filters(pool_request)
        if "budget" in inspect.signature(self._core.recall).parameters:
            kwargs["budget"] = budget
        recalled = await self._core.recall(request.query, **kwargs)
        kept = [
            item
            for item in recalled.results
            if (item.source_type == "conversation")
            == (source_type == "conversation")
        ][:limit]
        return RecallResult(
            results=kept,
            chunks=recalled.chunks,
            entities=recalled.entities,
            documents=recalled.documents,
            trace={
                **recalled.trace,
                "source_pool": source_type,
                "source_leakage_filtered": len(recalled.results) - len(kept),
            },
        )

    @staticmethod
    def _merge_source_pools(
        document_result: RecallResult,
        conversation_result: RecallResult,
        *,
        route: str,
        document_quota: int,
        conversation_quota: int,
        auxiliary: bool,
    ) -> RecallResult:
        return RecallResult(
            results=[*document_result.results, *conversation_result.results],
            chunks={**document_result.chunks, **conversation_result.chunks},
            entities={**document_result.entities, **conversation_result.entities},
            documents={**document_result.documents, **conversation_result.documents},
            trace={
                **document_result.trace,
                "route": route,
                "auxiliary_memory_context": auxiliary,
                "source_pool_quotas": {
                    "upload": document_quota,
                    "conversation": conversation_quota,
                },
                "source_pool_counts": {
                    "upload": len(document_result.results),
                    "conversation": len(conversation_result.results),
                },
                "source_pool_traces": {
                    "upload": document_result.trace,
                    "conversation": conversation_result.trace,
                },
            },
        )

    @staticmethod
    def _enforce_route_candidates(
        recalled: RecallResult, route: str
    ) -> RecallResult:
        if route in {"knowledge", "mixed"}:
            return recalled
        kept = [
            item
            for item in recalled.results
            if (
                item.source_type == "conversation"
                if route == "conversation"
                else item.source_type != "conversation"
            )
        ]
        leaked = len(recalled.results) - len(kept)
        return RecallResult(
            results=kept,
            chunks=recalled.chunks,
            entities=recalled.entities,
            documents=recalled.documents,
            trace={**recalled.trace, "source_leakage_filtered": leaked},
        )

    @staticmethod
    def _collapse_and_cap(recalled: RecallResult) -> RecallResult:
        """Collapse derived duplicates and prevent one identity flooding results.

        Candidate ordering is already strongest-first. Keeping the first item
        therefore preserves the best representative without comparing raw scores
        across source pools.
        """
        from config.settings import settings

        selected: list[RecallCandidate] = []
        seen: set[tuple[str, ...]] = set()
        document_counts: defaultdict[str, int] = defaultdict(int)
        turn_counts: defaultdict[str, int] = defaultdict(int)
        duplicate_count = 0
        cap_count = 0
        for item in recalled.results:
            normalized = re.sub(
                r"\W+", " ", str(item.source_text or item.text or "").casefold()
            ).strip()
            if item.source_type == "conversation" and item.turn_id:
                identity = ("turn", item.session_id or "", item.turn_id, normalized)
            else:
                identity = (
                    "document",
                    item.document_id,
                    str(item.chunk_index),
                    normalized,
                )
            if identity in seen:
                duplicate_count += 1
                continue
            if item.source_type == "conversation" and item.turn_id:
                turn_key = f"{item.session_id or ''}:{item.turn_id}"
                if turn_counts[turn_key] >= settings.hindsight_max_memories_per_turn:
                    cap_count += 1
                    continue
                turn_counts[turn_key] += 1
            else:
                if (
                    document_counts[item.document_id]
                    >= settings.hindsight_max_passages_per_document
                ):
                    cap_count += 1
                    continue
                document_counts[item.document_id] += 1
            seen.add(identity)
            selected.append(item)
        hierarchical = dict(recalled.trace.get("hierarchical_retrieval") or {})
        if hierarchical:
            hierarchical.update(
                final_result_count=len(selected),
                final_document_coverage=len(
                    {item.document_id for item in selected if item.document_id}
                ),
                route_duplicate_collapsed=duplicate_count,
                route_cap_dropped=cap_count,
            )
        return RecallResult(
            results=selected,
            chunks=recalled.chunks,
            entities=recalled.entities,
            documents=recalled.documents,
            trace={
                **recalled.trace,
                "duplicate_collapsed": duplicate_count,
                "identity_cap_dropped": cap_count,
                "document_coverage": len(
                    {item.document_id for item in selected if item.document_id}
                ),
                **(
                    {"hierarchical_retrieval": hierarchical} if hierarchical else {}
                ),
            },
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
        if request.route not in {"knowledge", "conversation", "mixed"}:
            raise ValueError(f"unsupported query route: {request.route}")

    @staticmethod
    def _bound_evidence(
        recalled: RecallResult,
    ) -> tuple[list[RecallCandidate], dict[str, str], dict]:
        """Cap per-excerpt and total evidence chars for recall payloads.

        Results arrive ranked best-first, so the walk keeps a ranked prefix
        and drops the lowest-ranked evidence first. The trim lands in the
        trace (beside `degraded`) so telemetry stays truthful while the
        payload stays bounded.
        """
        from config.settings import settings

        excerpt_cap = settings.engine_tools_deep_excerpt_chars
        total_budget = settings.engine_tools_deep_total_chars
        kept: list[RecallCandidate] = []
        texts: dict[str, str] = {}
        original_chars = 0
        used = 0
        for item in recalled.results:
            chunk_id = f"{item.document_id}_{item.chunk_index}"
            raw = str(
                recalled.chunks.get(chunk_id, {}).get("text")
                or item.source_text
                or item.text
                or ""
            )
            original_chars += len(raw)
            text = raw[:excerpt_cap]
            if kept and used + len(text) > total_budget:
                break  # budget exhausted: lower-ranked evidence drops first
            if not kept and len(text) > total_budget:
                text = text[:total_budget]
            kept.append(item)
            texts[item.id] = text
            used += len(text)
        trace = {
            "evidence_trimmed": used < original_chars,
            "evidence_kept": len(kept),
            "evidence_dropped": len(recalled.results) - len(kept),
            "evidence_chars": used,
        }
        return kept, texts, trace

    @staticmethod
    def _resolve_strategy(request: KnowledgeQueryRequest) -> str:
        # A mixed route is evidence retrieval by contract: source-local pools
        # must remain inspectable and the calling agent performs synthesis.
        if request.route == "mixed":
            return "recall"
        if request.strategy != "auto":
            return request.strategy
        return "reflect" if request.needs_answer else "recall"

    @staticmethod
    def _filters(request: KnowledgeQueryRequest) -> RecallFilter | None:
        source_types = request.source_types or (
            ("conversation",) if request.route == "conversation" else ("upload",)
        )
        extended = any(
            (
                request.memory_types,
                source_types,
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
            source_types=source_types,
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
        item: RecallCandidate, chunk_text: str
    ) -> KnowledgeSource:
        # Identifying fields only — the full document record stays behind
        # `tkb_get_document`; a response must not embed it per source.
        return KnowledgeSource(
            memory_id=item.id,
            memory_type=item.memory_type,
            doc_id=item.document_id,
            title=item.title,
            chunk_text=chunk_text,
            score=item.final_score,
            authority=(
                "conversation" if item.source_type == "conversation" else "document"
            ),
            source_group=(
                "conversation_context"
                if item.source_type == "conversation"
                else "document_evidence"
            ),
            provenance={
                "memory_id": item.id,
                "document_id": item.document_id,
                **({"session_id": item.session_id} if item.session_id else {}),
                **({"turn_id": item.turn_id} if item.turn_id else {}),
            },
            metadata={
                **dict(item.metadata),
                "source_type": item.source_type,
                "mentioned_at": item.mentioned_at,
                "updated_at": item.updated_at,
                "occurred_start": item.occurred_start,
                "occurred_end": item.occurred_end,
                "freshness": item.freshness,
                "stale_reason": item.stale_reason,
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
        from config.settings import settings

        limit = settings.engine_tools_entities_max
        output = []
        for name, state in list(entities.items())[:limit]:
            if isinstance(state, Mapping):
                output.append({"name": name, **dict(state)})
            else:
                output.append({"name": name, "state": state})
        return output

    @staticmethod
    def _compact_based_on(
        based_on: Mapping[str, list[dict]],
    ) -> dict[str, list[dict]]:
        """Grouped provenance without repeating the per-source evidence.

        Memory groups duplicate what `sources` already carries excerpt-by-
        excerpt; they keep identity and scores. Model/directive/citation
        groups never appear in sources and pass through unchanged.
        """
        passthrough = {
            "directives",
            "mental_models",
            "retrieved_mental_models",
            "actual_citations",
        }
        compact: dict[str, list[dict]] = {}
        for group, items in based_on.items():
            if group in passthrough:
                compact[group] = list(items)
                continue
            compact[group] = [
                {key: value for key, value in item.items() if key != "text"}
                for item in items
            ]
        return compact

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
                        authority=(
                            "conversation"
                            if metadata.get("source_type") == "conversation"
                            else "document"
                        ),
                        source_group=(
                            "conversation_context"
                            if metadata.get("source_type") == "conversation"
                            else "document_evidence"
                        ),
                        provenance={
                            "memory_id": memory_id,
                            "document_id": str(item.get("document_id", "")),
                            **(
                                {"session_id": item["session_id"]}
                                if item.get("session_id")
                                else {}
                            ),
                            **(
                                {"turn_id": item["turn_id"]}
                                if item.get("turn_id")
                                else {}
                            ),
                        },
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
        adaptive_deep_search_enabled=settings.hindsight_adaptive_deep_search_enabled,
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
        recall_min_term_coverage=settings.hindsight_recall_min_term_coverage,
        recall_min_term_count=settings.hindsight_recall_min_term_count,
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
    return HindsightQueryService(
        core,
        mixed_source_search_enabled=settings.hindsight_mixed_source_search_enabled,
        knowledge_memory_context_enabled=(
            settings.hindsight_knowledge_memory_context_enabled
        ),
        knowledge_memory_context_limit=(
            settings.hindsight_knowledge_memory_context_limit
        ),
    )
