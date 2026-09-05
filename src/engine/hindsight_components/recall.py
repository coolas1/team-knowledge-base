"""Recall: bounded multi-arm retrieval, rank fusion, reranking, and diversity."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from .config import HindsightOptions
from .deadlines import DeadlineBudget, PhaseStatus
from .errors import DeepSearchTimeoutError, DeepSearchUnavailableError
from .protocols import HindsightProviders, MemoryRepository
from .types import RecallCandidate, RecallResult
from .utils import cosine, estimate_tokens, parse_datetime

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
    ) -> None:
        self._repository = repository
        self._providers = providers
        self._options = options

    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        source_type: str | None = None,
        search_id: str | None = None,
    ) -> RecallResult:
        if mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {mode}")
        if not query.strip():
            raise ValueError("query cannot be empty")
        limit = top_k if top_k is not None else self._options.recall_limit
        if limit < 1:
            raise ValueError("top_k must be greater than zero")

        identifier = search_id or str(uuid.uuid4())
        budget = DeadlineBudget(self._options.deep_total_timeout_seconds)
        phase_outcomes: dict[str, dict[str, Any]] = {}
        phase_ms: dict[str, float] = {}
        terminal = "failed"
        candidate_count = 0
        selected_count = 0
        try:
            analysis, embedding = await self._prepare_query(
                query, mode, identifier, budget, phase_outcomes, phase_ms
            )
            arms = await self._retrieve_arms(
                query,
                mode,
                source_type,
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
                trace = self._failure_trace(
                    identifier, mode, budget, phase_outcomes, phase_ms
                )
                timed_out = any(
                    item["outcome"] == PhaseStatus.TIMED_OUT.value
                    for item in phase_outcomes.values()
                )
                if timed_out:
                    raise DeepSearchTimeoutError(identifier, trace)
                raise DeepSearchUnavailableError(identifier, trace)

            names = ("semantic", "keyword", "graph", "temporal")
            candidates: dict[str, RecallCandidate] = {}
            rrf: defaultdict[str, float] = defaultdict(float)
            for arm_name, rows in zip(names, arms, strict=True):
                for rank, raw in enumerate(rows, start=1):
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
                    rrf[candidate.id] += 1 / (self._options.rrf_k + rank)

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
                mode,
                identifier,
                budget,
                phase_outcomes,
                phase_ms,
            )
            ordered, filtered_count = self._filter_by_relevance(ordered, mode)
            selected, token_count, selection_ms = self._select(ordered, limit)
            selected_count = len(selected)
            phase_ms["mmr_token_selection"] = selection_ms
            self._record_local_phase(
                "mmr_token_selection",
                PhaseStatus.SUCCEEDED if selected else PhaseStatus.EMPTY,
                selection_ms,
                phase_outcomes,
            )

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
            chunks = {
                f"{item.document_id}_{item.chunk_index}": {
                    "id": f"{item.document_id}_{item.chunk_index}",
                    "text": item.source_text,
                    "chunk_index": item.chunk_index,
                }
                for item in selected
            }
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
                "analysis": analysis,
                "arm_counts": dict(zip(names, map(len, arms), strict=True)),
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "filtered_count": filtered_count,
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
                "fallback": None,
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
    ) -> tuple[dict[str, Any], list[float] | None]:
        if mode == "deep":
            async with asyncio.TaskGroup() as group:
                analysis_task = group.create_task(
                    self._run_phase(
                        "query_analysis_llm",
                        lambda timeout: self._analyze_query(query, timeout),
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
                {"entities": [], "start": None, "end": None, "subqueries": []},
                PhaseStatus.SKIPPED,
            )
            self._record_local_phase(
                "query_analysis_llm", PhaseStatus.SKIPPED, 0.0, phase_outcomes
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
        embeddings = embedding_phase.value or []
        return analysis, embeddings[0] if embeddings else None

    async def _retrieve_arms(
        self,
        query: str,
        mode: str,
        source_type: str | None,
        limit: int,
        analysis: dict[str, Any],
        embedding: list[float] | None,
        search_id: str,
        budget: DeadlineBudget,
        phase_outcomes: dict[str, dict[str, Any]],
        phase_ms: dict[str, float],
    ) -> list[list[RecallCandidate]]:
        arm_limit = max(limit * 3, self._options.retrieval_arm_minimum)
        factories: dict[str, Callable[[float], Awaitable[list[RecallCandidate]]]] = {
            "bm25_search": lambda _timeout: self._repository.keyword_search(
                query, arm_limit, source_type=source_type
            )
        }
        if embedding is not None:
            factories["semantic_search"] = lambda _timeout: (
                self._repository.semantic_search(
                    embedding, arm_limit, source_type=source_type
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
        }
        if mode == "deep" and analysis_ok:
            factories["graph_expansion"] = lambda _timeout: (
                self._repository.graph_search(
                    [str(item) for item in analysis.get("entities", [])],
                    arm_limit,
                    source_type=source_type,
                )
            )
            factories["temporal_search"] = lambda _timeout: (
                self._repository.temporal_search(
                    parse_datetime(analysis.get("start")),
                    parse_datetime(analysis.get("end")),
                    arm_limit,
                    source_type=source_type,
                )
            )
        else:
            reason = "fast_mode" if mode == "fast" else "analysis_unavailable"
            for name in ("graph_expansion", "temporal_search"):
                self._record_local_phase(
                    name, PhaseStatus.SKIPPED, 0.0, phase_outcomes, reason
                )
                phase_ms[name] = 0.0

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

    async def _analyze_query(self, query: str, timeout: float) -> dict[str, Any]:
        return await self._providers.json(
            "Analyze a memory retrieval query. Identify named entities, time bounds, and missing hops.",
            f"QUERY: {query}\n"
            'Return {"entities":[],"start":"ISO or null","end":"ISO or null",'
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
        if mode == "fast":
            self._record_local_phase(
                "neural_rerank_llm",
                PhaseStatus.SKIPPED,
                0.0,
                phase_outcomes,
                "fast_mode",
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
        self, ordered: list[RecallCandidate], mode: str
    ) -> tuple[list[RecallCandidate], int]:
        kept: list[RecallCandidate] = []
        for item in ordered:
            if (item.keyword_score or 0.0) > 0:
                kept.append(item)
                continue
            if mode == "deep" and item.reranker_score is not None:
                if item.final_score < self._options.recall_min_score:
                    continue
            elif (item.semantic_score or 0.0) < self._options.recall_min_semantic:
                continue
            kept.append(item)
        return kept, len(ordered) - len(kept)

    def _select(
        self, ordered: list[RecallCandidate], limit: int
    ) -> tuple[list[RecallCandidate], int, float]:
        started = time.perf_counter()
        remaining = list(ordered)
        selected: list[RecallCandidate] = []
        token_count = 0
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
            size = estimate_tokens(best.source_text)
            if selected and token_count + size > self._options.recall_max_tokens:
                continue
            selected.append(best)
            token_count += size
        return selected, token_count, round((time.perf_counter() - started) * 1000, 2)

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
                and value.get("category") != "fast_mode"
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
