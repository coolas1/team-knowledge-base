"""Recall: multi-arm retrieval, rank fusion, reranking, and diversity."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import replace
from typing import Any

from .config import HindsightOptions
from .protocols import HindsightProviders, MemoryRepository
from .types import RecallCandidate, RecallResult
from .utils import cosine, estimate_tokens, parse_datetime


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
    ) -> RecallResult:
        if mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {mode}")
        if not query.strip():
            raise ValueError("query cannot be empty")
        limit = top_k if top_k is not None else self._options.recall_limit
        if limit < 1:
            raise ValueError("top_k must be greater than zero")

        started = time.perf_counter()
        phase_ms: dict[str, float] = {}

        async def timed(name: str, awaitable: Any) -> Any:
            phase_started = time.perf_counter()
            try:
                return await awaitable
            finally:
                phase_ms[name] = round((time.perf_counter() - phase_started) * 1000, 2)

        if mode == "deep":
            analysis, embeddings = await asyncio.gather(
                timed("query_analysis_llm", self._analyze_query(query)),
                timed("query_embedding", self._providers.embed([query])),
            )
        else:
            analysis = {"entities": [], "start": None, "end": None, "subqueries": []}
            phase_ms["query_analysis_llm"] = 0.0
            embeddings = await timed("query_embedding", self._providers.embed([query]))
        if not embeddings:
            raise ValueError("embedding provider returned no query embedding")
        query_embedding = embeddings[0]
        arm_limit = max(limit * 3, self._options.retrieval_arm_minimum)

        if mode == "deep":
            arms = await asyncio.gather(
                timed(
                    "semantic_search",
                    self._repository.semantic_search(
                        query_embedding,
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
                timed(
                    "bm25_search",
                    self._repository.keyword_search(
                        query,
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
                timed(
                    "graph_expansion",
                    self._repository.graph_search(
                        [str(item) for item in analysis.get("entities", [])],
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
                timed(
                    "temporal_search",
                    self._repository.temporal_search(
                        parse_datetime(analysis.get("start")),
                        parse_datetime(analysis.get("end")),
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
            )
        else:
            semantic, keyword = await asyncio.gather(
                timed(
                    "semantic_search",
                    self._repository.semantic_search(
                        query_embedding,
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
                timed(
                    "bm25_search",
                    self._repository.keyword_search(
                        query,
                        arm_limit,
                        source_type=source_type,
                    ),
                ),
            )
            phase_ms["graph_expansion"] = 0.0
            phase_ms["temporal_search"] = 0.0
            arms = [semantic, keyword, [], []]

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
                setattr(candidate, f"{arm_name}_score", self._raw_score(raw, arm_name))
                candidate.source_ranks[arm_name] = rank
                rrf[candidate.id] += 1 / (self._options.rrf_k + rank)

        ordered = sorted(
            candidates.values(), key=lambda item: rrf[item.id], reverse=True
        )[: self._options.rerank_limit]
        await self._rerank(query, ordered, rrf, mode, phase_ms)
        selected, token_count, selection_ms = self._select(ordered, limit)
        phase_ms["mmr_token_selection"] = selection_ms
        entities = await timed(
            "entity_state_load",
            self._repository.entity_states([item.id for item in selected]),
        )
        chunks = {
            f"{item.document_id}_{item.chunk_index}": {
                "id": f"{item.document_id}_{item.chunk_index}",
                "text": item.source_text,
                "chunk_index": item.chunk_index,
            }
            for item in selected
        }
        return RecallResult(
            results=selected,
            chunks=chunks,
            entities=entities,
            trace={
                "query": query,
                "mode": mode,
                "source_type": source_type,
                "analysis": analysis,
                "arm_counts": dict(zip(names, map(len, arms), strict=True)),
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "token_count": token_count,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "phase_ms": phase_ms,
                "algorithm": (
                    "semantic+BM25/RRF/MMR"
                    if mode == "fast"
                    else "semantic+BM25+graph-link-expansion+temporal/RRF/neural-rerank/MMR"
                ),
            },
        )

    async def _analyze_query(self, query: str) -> dict[str, Any]:
        try:
            return await self._providers.json(
                "Analyze a memory retrieval query. Identify named entities, time bounds, and missing hops.",
                f"QUERY: {query}\n"
                'Return {"entities":[],"start":"ISO or null","end":"ISO or null",'
                '"subqueries":[]}.',
            )
        except Exception:
            return {"entities": [], "start": None, "end": None, "subqueries": []}

    @staticmethod
    def _raw_score(candidate: RecallCandidate, arm_name: str) -> float:
        value = getattr(candidate, f"{arm_name}_score", None)
        return float(value or 0.0)

    async def _rerank(
        self,
        query: str,
        ordered: list[RecallCandidate],
        rrf: dict[str, float],
        mode: str,
        phase_ms: dict[str, float],
    ) -> None:
        if not ordered or mode == "fast":
            phase_ms["neural_rerank_llm"] = 0.0
            for item in ordered:
                item.final_score = rrf[item.id]
            return
        started = time.perf_counter()
        try:
            payload = await self._providers.json(
                "Rank memories by direct usefulness for answering the query. Return every supplied id once.",
                f"QUERY: {query}\nMEMORIES:\n"
                + "\n".join(f"{item.id}: {item.text}" for item in ordered)
                + '\nReturn {"ranking":[{"id":"...","score":0..1}]}.',
            )
            scores = {
                str(item["id"]): float(item["score"])
                for item in payload.get("ranking", [])
            }
        except Exception:
            scores = {}
        finally:
            phase_ms["neural_rerank_llm"] = round(
                (time.perf_counter() - started) * 1000, 2
            )
        for item in ordered:
            item.reranker_score = scores.get(item.id)
            item.final_score = (
                item.reranker_score if item.reranker_score is not None else rrf[item.id]
            )
        ordered.sort(key=lambda item: item.final_score, reverse=True)

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
                    )
                ),
            )
            remaining.remove(best)
            size = estimate_tokens(best.source_text)
            if selected and token_count + size > self._options.recall_max_tokens:
                continue
            selected.append(best)
            token_count += size
        return selected, token_count, round((time.perf_counter() - started) * 1000, 2)
