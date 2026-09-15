"""Recall: bounded multi-arm retrieval, rank fusion, reranking, and diversity."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from .config import HindsightOptions
from .fact_cache import cache_key
from .deadlines import DeadlineBudget, PhaseStatus
from .errors import DeepSearchTimeoutError, DeepSearchUnavailableError
from .protocols import HindsightProviders, MemoryRepository
from .types import RecallCandidate, RecallFilter, RecallResult
from .utils import cosine, estimate_tokens, lexical_tokens, parse_datetime

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(slots=True)
class _PhaseResult:
    value: Any
    status: PhaseStatus


class RecallEngine:
    def __init__(
        self,
        repository: MemoryRepository,
        providers: HindsightProviders,
        options: HindsightOptions,
        *,
        fact_cache=None,
    ) -> None:
        self._repository = repository
        self._providers = providers
        self._options = options
        self._fact_cache = fact_cache

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
        if mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {mode}")
        if not query.strip():
            raise ValueError("query cannot be empty")
        requested_limit = top_k if top_k is not None else self._options.recall_limit
        limit = min(requested_limit, self._options.recall_max_results)
        if limit < 1:
            raise ValueError("top_k must be greater than zero")

        identifier = search_id or str(uuid.uuid4())
        filters = filters or RecallFilter(
            source_types=(source_type,) if source_type else ()
        )
        if source_type and source_type not in filters.source_types:
            filters = replace(
                filters, source_types=(*filters.source_types, source_type)
            )
        expire_due = getattr(self._repository, "expire_due_memories", None)
        if expire_due is not None:
            await expire_due()
        budget = DeadlineBudget(
            min(
                self._options.deep_total_timeout_seconds,
                filters.timeout_seconds or self._options.deep_total_timeout_seconds,
            )
        )
        token_limit = min(
            self._options.recall_max_tokens,
            filters.max_tokens or self._options.recall_max_tokens,
        )
        phase_outcomes: dict[str, dict[str, Any]] = {}
        phase_ms: dict[str, float] = {}
        terminal = "failed"
        candidate_count = 0
        selected_count = 0
        fallback: str | None = None
        try:
            analysis, embedding = await self._prepare_query(
                query, mode, identifier, budget, phase_outcomes, phase_ms, filters
            )
            arms = await self._retrieve_arms(
                query,
                mode,
                source_type,
                filters,
                limit,
                analysis,
                embedding,
                identifier,
                budget,
                phase_outcomes,
                phase_ms,
            )
            available_arms = [
                name
                for name in (
                    "semantic_search",
                    "bm25_search",
                    "graph_expansion",
                    "temporal_search",
                )
                if phase_outcomes[name]["outcome"]
                in {PhaseStatus.SUCCEEDED.value, PhaseStatus.EMPTY.value}
            ]
            if not available_arms:
                fallback_result = None
                if mode == "deep" and filters.source_types != ("conversation",):
                    fallback_filter = replace(filters, source_types=("upload",))
                    fallback_phase = await self._run_phase(
                        "document_index_fallback",
                        lambda _timeout: self._repository.keyword_search(
                            query,
                            limit,
                            source_type="upload",
                            filters=fallback_filter,
                        ),
                        self._options.retrieval_arm_timeout_seconds,
                        identifier,
                        budget,
                        phase_outcomes,
                        phase_ms,
                    )
                    fallback_result = fallback_phase.value or []
                    if fallback_result:
                        arms[1] = fallback_result
                        available_arms = ["document_index_fallback"]
                        fallback = "document_index"
                trace = self._failure_trace(
                    identifier, mode, budget, phase_outcomes, phase_ms
                )
                if not fallback_result:
                    timed_out = any(
                        item["outcome"] == PhaseStatus.TIMED_OUT.value
                        for item in phase_outcomes.values()
                    )
                    if timed_out:
                        raise DeepSearchTimeoutError(identifier, trace)
                    raise DeepSearchUnavailableError(identifier, trace)
            elif mode == "deep":
                self._record_local_phase(
                    "document_index_fallback",
                    PhaseStatus.SKIPPED,
                    0.0,
                    phase_outcomes,
                    "primary_evidence_available",
                )
                phase_ms["document_index_fallback"] = 0.0

            names = ("semantic", "keyword", "graph", "temporal")
            candidates: dict[str, RecallCandidate] = {}
            rrf: defaultdict[str, float] = defaultdict(float)
            pool_ranks: defaultdict[tuple[str, str], int] = defaultdict(int)
            route_weights = {
                "upload": dict(zip(names, self._options.knowledge_arm_weights)),
                "conversation": dict(
                    zip(names, self._options.conversation_arm_weights)
                ),
            }
            for arm_name, rows in zip(names, arms, strict=True):
                for raw in rows:
                    pool = (
                        "conversation"
                        if raw.source_type == "conversation"
                        else "upload"
                    )
                    pool_ranks[(pool, arm_name)] += 1
                    rank = pool_ranks[(pool, arm_name)]
                    candidate = candidates.get(raw.id)
                    if candidate is None:
                        candidate = replace(
                            raw,
                            metadata=dict(raw.metadata),
                            source_memory_ids=list(raw.source_memory_ids),
                            embedding=list(raw.embedding) if raw.embedding else None,
                            source_ranks=dict(raw.source_ranks),
                        )
                        candidates[raw.id] = candidate
                    setattr(
                        candidate, f"{arm_name}_score", self._raw_score(raw, arm_name)
                    )
                    candidate.source_ranks[arm_name] = rank
                    candidate.source_ranks[f"{pool}:{arm_name}"] = rank
                    rrf[candidate.id] += route_weights[pool][arm_name] / (
                        self._options.rrf_k + rank
                    )

            if not candidates:
                failed_retrieval = [
                    name
                    for name in (
                        "semantic_search",
                        "bm25_search",
                        "graph_expansion",
                        "temporal_search",
                    )
                    if phase_outcomes[name]["outcome"]
                    in {PhaseStatus.TIMED_OUT.value, PhaseStatus.FAILED.value}
                ]
                if failed_retrieval:
                    trace = self._failure_trace(
                        identifier, mode, budget, phase_outcomes, phase_ms
                    )
                    if any(
                        phase_outcomes[name]["outcome"] == PhaseStatus.TIMED_OUT.value
                        for name in failed_retrieval
                    ):
                        raise DeepSearchTimeoutError(identifier, trace)
                    raise DeepSearchUnavailableError(identifier, trace)

            candidates, duplicate_collapsed = self._collapse_candidates(candidates, rrf)
            ordered = sorted(
                candidates.values(), key=lambda item: (-rrf[item.id], item.id)
            )[: self._options.rerank_limit]
            candidate_count = len(candidates)
            (
                rerank_truncated,
                ranking_method,
                rerank_submitted_count,
            ) = await self._rerank(
                query,
                ordered,
                rrf,
                "fast" if fallback else mode,
                identifier,
                budget,
                phase_outcomes,
                phase_ms,
            )
            ordered, filtered_count = self._filter_by_relevance(
                ordered, mode, filters, query
            )
            ordered, conversation_quality_filtered = (
                self._apply_conversation_quality_factors(
                    ordered,
                    reference_time=filters.reference_time,
                    include_stale=filters.include_stale,
                )
            )
            if filters.prefer_observations:
                for item in ordered:
                    if item.memory_type == "observation":
                        item.final_score = min(1.0, item.final_score + 0.05)
                ordered.sort(key=lambda item: (-item.final_score, item.id))
            selected, token_count, selection_ms = self._select(
                ordered, limit, token_limit
            )
            selected_count = len(selected)
            phase_ms["mmr_token_selection"] = selection_ms
            self._record_local_phase(
                "mmr_token_selection",
                PhaseStatus.SUCCEEDED if selected else PhaseStatus.EMPTY,
                selection_ms,
                phase_outcomes,
            )

            details_loader = getattr(self._repository, "recall_details", None)
            if details_loader is not None:
                detail_phase = await self._run_phase(
                    "evidence_state_load",
                    lambda _timeout: details_loader(
                        [item.id for item in selected],
                        include_source_facts="source_facts" in filters.include,
                    ),
                    self._options.retrieval_arm_timeout_seconds,
                    identifier,
                    budget,
                    phase_outcomes,
                    phase_ms,
                )
                for item in selected:
                    detail = (detail_phase.value or {}).get(item.id, {})
                    item.freshness = str(detail.get("freshness", item.freshness))
                    item.stale_reason = detail.get("stale_reason")
                    item.updated_at = detail.get("updated_at", item.mentioned_at)
                    if "source_facts" in filters.include:
                        item.metadata["source_facts"] = list(
                            detail.get("source_facts", [])
                        )

            if any(item.source_type == "conversation" for item in selected):
                before_quality = len(selected)
                selected = [
                    item
                    for item in selected
                    if item.source_type != "conversation"
                    or self._conversation_quality_ok(
                        item, include_stale=filters.include_stale
                    )
                ]
                conversation_quality_filtered += before_quality - len(selected)
                selected_count = len(selected)
                token_count = sum(
                    estimate_tokens(item.source_text) for item in selected
                )

            if self._fact_cache is not None:
                scope = getattr(self._repository, "scope", id(self._repository))
                self._fact_cache.remember(
                    cache_key(scope, filters),
                    [
                        item.as_evidence()
                        for item in selected
                        if item.freshness in {"active", "current"}
                    ],
                )

            if "entities" in filters.include:
                entity_phase = await self._run_phase(
                    "entity_state_load",
                    lambda _timeout: self._repository.entity_states(
                        [item.id for item in selected]
                    ),
                    self._options.retrieval_arm_timeout_seconds,
                    identifier,
                    budget,
                    phase_outcomes,
                    phase_ms,
                )
                entities = entity_phase.value or {}
            else:
                entities = {}
                self._record_local_phase(
                    "entity_state_load",
                    PhaseStatus.SKIPPED,
                    0,
                    phase_outcomes,
                    "not_requested",
                )
                phase_ms["entity_state_load"] = 0
            chunks = (
                {
                    f"{item.document_id}_{item.chunk_index}": {
                        "id": f"{item.document_id}_{item.chunk_index}",
                        "text": item.source_text,
                        "chunk_index": item.chunk_index,
                    }
                    for item in selected
                }
                if "chunks" in filters.include
                else {}
            )
            documents = (
                {
                    item.document_id: {
                        "id": item.document_id,
                        "title": item.title,
                        "source_type": item.source_type,
                    }
                    for item in selected
                }
                if "documents" in filters.include
                else {}
            )
            degraded_phases = self._degraded_phases(mode, phase_outcomes)
            degraded = bool(degraded_phases)
            terminal = (
                "degraded"
                if degraded and selected
                else "empty"
                if not selected
                else "success"
            )
            trace = {
                "query": query,
                "mode": mode,
                "source_type": source_type,
                "filters": {
                    "memory_types": list(filters.memory_types),
                    "source_types": list(filters.source_types),
                    "include": list(filters.include),
                    "prefer_observations": filters.prefer_observations,
                    "include_stale": filters.include_stale,
                },
                "requested_top_k": requested_limit,
                "effective_top_k": limit,
                "token_budget": token_limit,
                "analysis": analysis,
                "arm_counts": dict(zip(names, map(len, arms), strict=True)),
                "source_local_fusion": {
                    "method": "weighted_rrf",
                    "rrf_k": self._options.rrf_k,
                    "route_weights": route_weights,
                    "pool_arm_counts": {
                        f"{pool}:{arm}": count
                        for (pool, arm), count in sorted(pool_ranks.items())
                    },
                },
                "candidate_count": len(candidates),
                "duplicate_collapsed": duplicate_collapsed,
                "selected_count": len(selected),
                "filtered_count": filtered_count,
                "conversation_quality_filtered": conversation_quality_filtered,
                "token_count": token_count,
                "duration_ms": budget.elapsed_ms(),
                "phase_ms": phase_ms,
                "algorithm": (
                    "semantic+BM25/RRF/MMR"
                    if mode == "fast"
                    else "semantic+BM25+graph-link-expansion+temporal/RRF/neural-rerank/MMR"
                ),
                "search_id": identifier,
                "outcome": terminal,
                "degraded": degraded,
                "degraded_phases": degraded_phases,
                "phase_outcomes": phase_outcomes,
                "fallback": fallback,
                "ranking_method": ranking_method,
                "rerank_truncated": rerank_truncated,
                "rerank_original_count": len(ordered),
                "rerank_submitted_count": rerank_submitted_count,
            }
            return RecallResult(
                results=selected,
                chunks=chunks,
                entities=entities,
                trace=trace,
                documents=documents,
            )
        except asyncio.CancelledError:
            terminal = "cancelled"
            raise
        except (DeepSearchTimeoutError, DeepSearchUnavailableError) as error:
            terminal = error.trace.get("outcome", error.code)
            raise
        finally:
            logger.info(
                "hindsight.deep_search.complete",
                extra={
                    "search_id": identifier,
                    "search_mode": mode,
                    "search_outcome": terminal,
                    "elapsed_ms": budget.elapsed_ms(),
                    "candidate_count": candidate_count,
                    "result_count": selected_count,
                    "total_timeout_seconds": self._options.deep_total_timeout_seconds,
                    "phase_outcomes": {
                        name: value["outcome"] for name, value in phase_outcomes.items()
                    },
                    "fallback": fallback,
                },
            )

    async def _prepare_query(
        self,
        query: str,
        mode: str,
        search_id: str,
        budget: DeadlineBudget,
        phase_outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
        filters: RecallFilter,
    ) -> tuple[dict[str, Any], list[float] | None]:
        features = self._query_features(query)
        run_analysis = mode == "deep" and (
            not self._options.adaptive_deep_search_enabled
            or features["graph"]
            or features["temporal"]
            or features["comparison"]
        )
        if run_analysis:
            async with asyncio.TaskGroup() as group:
                analysis_task = group.create_task(
                    self._run_phase(
                        "query_analysis_llm",
                        lambda timeout: self._analyze_query(
                            query, timeout, filters.reference_time
                        ),
                        self._options.query_analysis_timeout_seconds,
                        search_id,
                        budget,
                        phase_outcomes,
                        phase_ms,
                    )
                )
                embedding_task = group.create_task(
                    self._run_phase(
                        "query_embedding",
                        lambda timeout: self._providers.embed([query], timeout=timeout),
                        self._options.query_embedding_timeout_seconds,
                        search_id,
                        budget,
                        phase_outcomes,
                        phase_ms,
                    )
                )
            analysis_phase = analysis_task.result()
            embedding_phase = embedding_task.result()
        else:
            analysis_phase = _PhaseResult(
                {
                    "entities": [],
                    "start": None,
                    "end": None,
                    "subqueries": [],
                    "query_features": features,
                },
                PhaseStatus.SKIPPED,
            )
            self._record_local_phase(
                "query_analysis_llm",
                PhaseStatus.SKIPPED,
                0.0,
                phase_outcomes,
                "fast_mode" if mode == "fast" else "adaptive_simple_query",
            )
            phase_ms["query_analysis_llm"] = 0.0
            embedding_phase = await self._run_phase(
                "query_embedding",
                lambda timeout: self._providers.embed([query], timeout=timeout),
                self._options.query_embedding_timeout_seconds,
                search_id,
                budget,
                phase_outcomes,
                phase_ms,
            )

        analysis = analysis_phase.value or {
            "entities": [],
            "start": None,
            "end": None,
            "subqueries": [],
        }
        analysis.setdefault("query_features", features)
        embeddings = embedding_phase.value or []
        return analysis, embeddings[0] if embeddings else None

    async def _retrieve_arms(
        self,
        query: str,
        mode: str,
        source_type: str | None,
        filters: RecallFilter,
        limit: int,
        analysis: dict[str, Any],
        embedding: list[float] | None,
        search_id: str,
        budget: DeadlineBudget,
        phase_outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
    ) -> list[list[RecallCandidate]]:
        arm_limit = min(
            max(limit * 3, self._options.retrieval_arm_minimum),
            self._options.recall_max_candidates,
            filters.max_candidates or self._options.recall_max_candidates,
        )
        factories: dict[str, Callable[[float], Awaitable[list[RecallCandidate]]]] = {
            "bm25_search": lambda _timeout: self._repository.keyword_search(
                query, arm_limit, source_type=source_type, filters=filters
            )
        }
        if embedding is not None:
            factories["semantic_search"] = lambda _timeout: (
                self._repository.semantic_search(
                    embedding, arm_limit, source_type=source_type, filters=filters
                )
            )
        else:
            self._record_local_phase(
                "semantic_search",
                PhaseStatus.SKIPPED,
                0.0,
                phase_outcomes,
                "embedding_unavailable",
            )
            phase_ms["semantic_search"] = 0.0

        analysis_ok = phase_outcomes["query_analysis_llm"]["outcome"] in {
            PhaseStatus.SUCCEEDED.value,
            PhaseStatus.EMPTY.value,
        } or phase_outcomes["query_analysis_llm"].get("category") == (
            "adaptive_simple_query"
        )
        features = analysis.get("query_features", {})
        graph_needed = not self._options.adaptive_deep_search_enabled or bool(
            analysis.get("entities")
            or analysis.get("subqueries")
            or features.get("graph")
            or features.get("comparison")
        )
        temporal_needed = not self._options.adaptive_deep_search_enabled or bool(
            analysis.get("start") or analysis.get("end") or features.get("temporal")
        )
        if mode == "deep" and analysis_ok and graph_needed:
            factories["graph_expansion"] = lambda _timeout: (
                self._repository.graph_search(
                    [str(item) for item in analysis.get("entities", [])],
                    arm_limit,
                    source_type=source_type,
                    filters=filters,
                )
            )
        else:
            reason = (
                "fast_mode"
                if mode == "fast"
                else ("analysis_unavailable" if not analysis_ok else "not_required")
            )
            self._record_local_phase(
                "graph_expansion", PhaseStatus.SKIPPED, 0.0, phase_outcomes, reason
            )
            phase_ms["graph_expansion"] = 0.0
        if mode == "deep" and analysis_ok and temporal_needed:
            factories["temporal_search"] = lambda _timeout: (
                self._repository.temporal_search(
                    parse_datetime(analysis.get("start")),
                    parse_datetime(analysis.get("end")),
                    arm_limit,
                    source_type=source_type,
                    filters=filters,
                )
            )
        else:
            reason = (
                "fast_mode"
                if mode == "fast"
                else ("analysis_unavailable" if not analysis_ok else "not_required")
            )
            self._record_local_phase(
                "temporal_search", PhaseStatus.SKIPPED, 0.0, phase_outcomes, reason
            )
            phase_ms["temporal_search"] = 0.0

        tasks: dict[str, asyncio.Task[_PhaseResult]] = {}
        async with asyncio.TaskGroup() as group:
            for name, factory in factories.items():
                tasks[name] = group.create_task(
                    self._run_phase(
                        name,
                        factory,
                        self._options.retrieval_arm_timeout_seconds,
                        search_id,
                        budget,
                        phase_outcomes,
                        phase_ms,
                    )
                )
        values = {name: task.result().value or [] for name, task in tasks.items()}
        return [
            values.get("semantic_search", []),
            values.get("bm25_search", []),
            values.get("graph_expansion", []),
            values.get("temporal_search", []),
        ]

    async def _run_phase(
        self,
        name: str,
        factory: Callable[[float], Awaitable[T]],
        configured_timeout: float,
        search_id: str,
        budget: DeadlineBudget,
        phase_outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
    ) -> _PhaseResult:
        started = time.monotonic()
        timeout = budget.phase_timeout(configured_timeout)
        logger.info(
            "hindsight.deep_search.phase.start",
            extra={"search_id": search_id, "phase": name, "timeout_seconds": timeout},
        )
        value: T | None = None
        status = PhaseStatus.FAILED
        category: str | None = None
        try:
            if timeout <= 0:
                status = PhaseStatus.TIMED_OUT
                category = "total_deadline_exhausted"
            else:
                async with asyncio.timeout(timeout):
                    value = await factory(timeout)
                status = (
                    PhaseStatus.EMPTY
                    if self._is_empty(value)
                    else PhaseStatus.SUCCEEDED
                )
        except TimeoutError:
            status = PhaseStatus.TIMED_OUT
            category = "phase_timeout"
        except asyncio.CancelledError:
            status = PhaseStatus.CANCELLED
            category = "cancelled"
            raise
        except Exception as error:
            status = PhaseStatus.FAILED
            category = type(error).__name__
        finally:
            elapsed = round((time.monotonic() - started) * 1000, 2)
            phase_ms[name] = elapsed
            phase_outcomes[name] = {
                "outcome": status.value,
                "elapsed_ms": elapsed,
                **({"category": category} if category else {}),
            }
            logger.info(
                "hindsight.deep_search.phase.complete",
                extra={
                    "search_id": search_id,
                    "phase": name,
                    "phase_outcome": status.value,
                    "elapsed_ms": elapsed,
                    "failure_category": category,
                    "component": (
                        type(self._providers).__name__
                        if name
                        in {
                            "query_analysis_llm",
                            "query_embedding",
                            "neural_rerank_llm",
                        }
                        else type(self._repository).__name__
                    ),
                },
            )
        return _PhaseResult(value, status)

    async def _analyze_query(
        self, query: str, timeout: float, reference_time=None
    ) -> dict[str, Any]:
        return await self._providers.json(
            "Analyze a memory retrieval query. Identify named entities, time bounds, and missing hops.",
            f"QUERY: {query}\n"
            + (
                f"REFERENCE TIME: {reference_time.isoformat()}\n"
                if reference_time
                else ""
            )
            + 'Return {"entities":[],"start":"ISO or null","end":"ISO or null",'
            '"subqueries":[]}.',
            timeout=timeout,
        )

    async def _rerank(
        self,
        query: str,
        ordered: list[RecallCandidate],
        rrf: dict[str, float],
        mode: str,
        search_id: str,
        budget: DeadlineBudget,
        phase_outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
    ) -> tuple[bool, str, int]:
        for item in ordered:
            item.final_score = rrf[item.id]
        if not ordered:
            self._record_local_phase(
                "neural_rerank_llm", PhaseStatus.EMPTY, 0.0, phase_outcomes
            )
            phase_ms["neural_rerank_llm"] = 0.0
            return False, "rrf", 0
        deterministic_margin = (
            rrf[ordered[0].id] - rrf[ordered[1].id] if len(ordered) > 1 else 1.0
        )
        if mode == "fast" or (
            self._options.adaptive_deep_search_enabled
            and (
                len(ordered) <= 1
                or (
                    deterministic_margin >= 0.01
                    and not self._query_features(query)["comparison"]
                )
            )
        ):
            self._record_local_phase(
                "neural_rerank_llm",
                PhaseStatus.SKIPPED,
                0.0,
                phase_outcomes,
                "fast_mode" if mode == "fast" else "deterministic_evidence_sufficient",
            )
            phase_ms["neural_rerank_llm"] = 0.0
            return False, "rrf", 0

        payload_lines, supplied_ids, truncated = self._bounded_rerank_lines(ordered)
        phase = await self._run_phase(
            "neural_rerank_llm",
            lambda timeout: self._providers.json(
                "Rank memories by direct usefulness for answering the query. Return every supplied id once.",
                f"QUERY: {query}\nMEMORIES:\n"
                + "\n".join(payload_lines)
                + '\nReturn {"ranking":[{"id":"...","score":0..1}]}.',
                timeout=timeout,
            ),
            self._options.rerank_timeout_seconds,
            search_id,
            budget,
            phase_outcomes,
            phase_ms,
        )
        scores: dict[str, float] = {}
        if isinstance(phase.value, dict):
            try:
                scores = {
                    str(item["id"]): float(item["score"])
                    for item in phase.value.get("ranking", [])
                    if str(item.get("id")) in supplied_ids
                }
            except (KeyError, TypeError, ValueError):
                scores = {}
                phase_outcomes["neural_rerank_llm"]["outcome"] = (
                    PhaseStatus.FAILED.value
                )
                phase_outcomes["neural_rerank_llm"]["category"] = "invalid_ranking"

        for item in ordered:
            item.reranker_score = scores.get(item.id)
            if item.reranker_score is None:
                item.final_score = rrf[item.id]
            elif (item.keyword_score or 0.0) > 0:
                item.final_score = item.reranker_score
            else:
                item.final_score = min(
                    item.reranker_score,
                    (item.semantic_score or 0.0) + self._options.rerank_semantic_margin,
                )
        ordered.sort(key=lambda item: (-item.final_score, item.id))
        if not scores:
            return truncated, "rrf", len(supplied_ids)
        return (
            truncated,
            "neural+rrf" if len(scores) < len(ordered) else "neural",
            len(supplied_ids),
        )

    @staticmethod
    def _query_features(query: str) -> dict[str, bool]:
        text = query.casefold()
        temporal = bool(
            re.search(
                r"(?:\b(?:when|before|after|during|timeline|latest|current|20\d{2})\b|何时|什么时候|之前|之后|期间|时间线|最新|当前|いつ|前|後|タイムライン)",
                text,
            )
        )
        comparison = bool(
            re.search(
                r"(?:\b(?:compare|contrast|versus|vs\.?|across|synthesize|why)\b|比较|对比|跨文档|综合|为什么|比較|対比|なぜ)",
                text,
            )
        )
        graph = comparison or bool(
            re.search(
                r"(?:\b(?:relationship|related to|depends on|caused by|multi-hop)\b|关系|关联|依赖|导致|多跳|関係|依存)",
                text,
            )
        )
        return {"temporal": temporal, "comparison": comparison, "graph": graph}

    def _bounded_rerank_lines(
        self, ordered: list[RecallCandidate]
    ) -> tuple[list[str], set[str], bool]:
        lines: list[str] = []
        ids: set[str] = set()
        used = 0
        truncated = len(ordered) > self._options.rerank_candidate_limit
        for item in ordered[: self._options.rerank_candidate_limit]:
            text = item.text[: self._options.rerank_text_limit_chars]
            if len(text) < len(item.text):
                truncated = True
            prefix = f"{item.id}: "
            remaining = self._options.rerank_total_chars - used - len(prefix)
            if remaining <= 0:
                truncated = True
                break
            bounded = text[:remaining]
            if len(bounded) < len(text):
                truncated = True
            line = prefix + bounded
            lines.append(line)
            ids.add(item.id)
            used += len(line) + 1
            if len(bounded) < len(text):
                break
        return lines, ids, truncated

    @staticmethod
    def _raw_score(candidate: RecallCandidate, arm_name: str) -> float:
        value = getattr(candidate, f"{arm_name}_score", None)
        return float(value or 0.0)

    def _filter_by_relevance(
        self,
        ordered: list[RecallCandidate],
        mode: str,
        filters: RecallFilter,
        query: str,
    ) -> tuple[list[RecallCandidate], int]:
        # Conversation-memory recall uses its own, lower semantic floor so the
        # public-corpus gate does not determine what memories are recalled.
        min_semantic = (
            self._options.conversation_recall_min_semantic
            if filters.source_types == ("conversation",)
            else self._options.recall_min_semantic
        )
        query_terms = list(dict.fromkeys(lexical_tokens(query)))
        kept: list[RecallCandidate] = []
        for item in ordered:
            score_values = {
                "final": item.final_score,
                "semantic": item.semantic_score,
                "keyword": item.keyword_score,
                "graph": item.graph_score,
                "temporal": item.temporal_score,
                "reranker": item.reranker_score,
            }
            if any(
                float(score_values[name] or 0) < threshold
                for name, threshold in filters.min_scores.items()
            ):
                continue
            if (item.keyword_score or 0.0) > 0 and self._passes_term_coverage(
                item.title, item.text, query_terms
            ):
                kept.append(item)
                continue
            # Semantic floor applies in every mode; the deep-mode rerank-score
            # gate applies on top of it, never instead of it.
            if (item.semantic_score or 0.0) < min_semantic:
                continue
            if mode == "deep" and item.reranker_score is not None:
                if item.final_score < self._options.recall_min_score:
                    continue
            kept.append(item)
        return kept, len(ordered) - len(kept)

    def _passes_term_coverage(
        self, title: str, text: str, query_terms: list[str]
    ) -> bool:
        # A keyword-only candidate must cover a fraction of the query's
        # salient terms — a single shared token (a stopword, a surname) is
        # not evidence of relevance. The candidate's title counts as covered
        # text so a document is findable by its metadata when its body is
        # low quality. Single-term queries carry no coverage signal and fall
        # back to the semantic floor.
        if len(query_terms) < 2:
            return False
        candidate_tokens = set(lexical_tokens(f"{title}\n{text}"))
        matched = sum(1 for term in query_terms if term in candidate_tokens)
        if matched < self._options.recall_min_term_count:
            return False
        return matched / len(query_terms) >= self._options.recall_min_term_coverage

    @staticmethod
    def _conversation_quality_ok(
        item: RecallCandidate, *, include_stale: bool = False
    ) -> bool:
        metadata = item.metadata
        if not include_stale and item.freshness not in {"active", "current"}:
            return False
        if metadata.get("lifecycle_state") in {"superseded", "expired", "retired"}:
            return False
        if (
            metadata.get("origin") == "assistant"
            and metadata.get("authority") != "user_confirmed"
        ):
            return False
        return True

    def _apply_conversation_quality_factors(
        self,
        ordered: list[RecallCandidate],
        *,
        reference_time=None,
        include_stale: bool = False,
    ) -> tuple[list[RecallCandidate], int]:
        from datetime import datetime, timezone

        now = reference_time or datetime.now(timezone.utc)
        kept: list[RecallCandidate] = []
        filtered = 0
        for item in ordered:
            if item.source_type != "conversation":
                kept.append(item)
                continue
            if not self._conversation_quality_ok(item, include_stale=include_stale):
                filtered += 1
                continue
            metadata = item.metadata
            authority = str(metadata.get("authority") or "unclassified")
            authority_factor = {
                "user_confirmed": 1.0,
                "user_asserted": 0.9,
                "derived": 0.8,
                "unclassified": 0.7,
            }.get(authority, 0.7)
            confirmation_factor = (
                1.0
                if metadata.get("confirmed_by_turn_id") or authority == "user_confirmed"
                else self._options.conversation_unconfirmed_factor
            )
            origin_factor = (
                self._options.conversation_assistant_origin_factor
                if metadata.get("origin") == "assistant"
                else 1.0
            )
            freshness_factor = 1.0
            if item.mentioned_at:
                try:
                    mentioned = datetime.fromisoformat(
                        item.mentioned_at.replace("Z", "+00:00")
                    )
                    age_days = max(0.0, (now - mentioned).total_seconds() / 86400)
                    freshness_factor = max(
                        self._options.conversation_min_freshness_factor,
                        0.5
                        ** (
                            age_days
                            / self._options.conversation_freshness_half_life_days
                        ),
                    )
                except ValueError:
                    freshness_factor = self._options.conversation_min_freshness_factor
            factor = (
                authority_factor
                * confirmation_factor
                * origin_factor
                * freshness_factor
            )
            item.final_score *= factor
            metadata["conversation_quality"] = {
                "factor": factor,
                "freshness": freshness_factor,
                "authority": authority_factor,
                "confirmation": confirmation_factor,
                "origin": origin_factor,
            }
            kept.append(item)
        kept.sort(key=lambda item: (-item.final_score, item.id))
        return kept, filtered

    def _select(
        self, ordered: list[RecallCandidate], limit: int, token_limit: int | None = None
    ) -> tuple[list[RecallCandidate], int, float]:
        started = time.perf_counter()
        remaining = list(ordered)
        selected: list[RecallCandidate] = []
        token_count = 0
        document_counts: defaultdict[str, int] = defaultdict(int)
        turn_counts: defaultdict[str, int] = defaultdict(int)
        while remaining and len(selected) < limit:
            best = max(
                remaining,
                key=lambda item: (
                    item.final_score
                    - self._options.mmr_redundancy_penalty
                    * max(
                        (
                            cosine(item.embedding, chosen.embedding)
                            for chosen in selected
                        ),
                        default=0.0,
                    ),
                    item.id,
                ),
            )
            remaining.remove(best)
            if best.source_type == "conversation" and best.turn_id:
                key = f"{best.session_id or ''}:{best.turn_id}"
                if turn_counts[key] >= self._options.max_memories_per_turn:
                    continue
            elif (
                document_counts[best.document_id]
                >= self._options.max_passages_per_document
            ):
                continue
            size = estimate_tokens(best.source_text)
            if token_count + size > (token_limit or self._options.recall_max_tokens):
                continue
            selected.append(best)
            token_count += size
            if best.source_type == "conversation" and best.turn_id:
                turn_counts[f"{best.session_id or ''}:{best.turn_id}"] += 1
            else:
                document_counts[best.document_id] += 1
        return selected, token_count, round((time.perf_counter() - started) * 1000, 2)

    @staticmethod
    def _collapse_candidates(candidates, rrf):
        representatives: dict[tuple[str, ...], RecallCandidate] = {}
        collapsed = 0
        for item in candidates.values():
            normalized = " ".join(lexical_tokens(item.text))
            identity = (
                ("turn", item.session_id or "", item.turn_id or "", normalized)
                if item.source_type == "conversation" and item.turn_id
                else (
                    "document",
                    item.document_id,
                    str(item.chunk_index),
                    normalized,
                )
            )
            current = representatives.get(identity)
            if current is None or rrf[item.id] > rrf[current.id]:
                if current is not None:
                    collapsed += 1
                representatives[identity] = item
            else:
                collapsed += 1
        return {item.id: item for item in representatives.values()}, collapsed

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value is None or (
            isinstance(value, (list, tuple, dict, set, str, bytes)) and not value
        )

    @staticmethod
    def _record_local_phase(
        name: str,
        status: PhaseStatus,
        elapsed_ms: float,
        outcomes: dict[str, dict[str, Any]],
        category: str | None = None,
    ) -> None:
        outcomes[name] = {
            "outcome": status.value,
            "elapsed_ms": elapsed_ms,
            **({"category": category} if category else {}),
        }

    @staticmethod
    def _degraded_phases(mode: str, outcomes: dict[str, dict[str, Any]]) -> list[str]:
        if mode == "fast":
            return []
        return [
            name
            for name, value in outcomes.items()
            if value["outcome"]
            in {
                PhaseStatus.TIMED_OUT.value,
                PhaseStatus.FAILED.value,
                PhaseStatus.CANCELLED.value,
            }
            or (
                value["outcome"] == PhaseStatus.SKIPPED.value
                and value.get("category") not in {"fast_mode", "not_requested"}
            )
        ]

    @staticmethod
    def _failure_trace(
        search_id: str,
        mode: str,
        budget: DeadlineBudget,
        outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
    ) -> dict[str, Any]:
        timed_out = any(
            item["outcome"] == PhaseStatus.TIMED_OUT.value for item in outcomes.values()
        )
        return {
            "search_id": search_id,
            "mode": mode,
            "outcome": "deep_search_timeout"
            if timed_out
            else "deep_search_unavailable",
            "degraded": True,
            "degraded_phases": [
                name
                for name, value in outcomes.items()
                if value["outcome"]
                in {PhaseStatus.TIMED_OUT.value, PhaseStatus.FAILED.value}
            ],
            "phase_outcomes": outcomes,
            "phase_ms": phase_ms,
            "duration_ms": budget.elapsed_ms(),
            "fallback": None,
        }
