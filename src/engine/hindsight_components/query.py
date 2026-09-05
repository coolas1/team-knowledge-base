"""Unified recall/reflect query adapter for the public engine contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Protocol

from src.engine.interface import (
    KnowledgeQueryRequest,
    KnowledgeQueryResult,
    KnowledgeSource,
)

from .config import HindsightOptions
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService
from .types import RecallCandidate, RecallResult, ReflectResult


class CoreQueryService(Protocol):
    async def recall(
        self,
        query: str,
        *,
        mode: str = "deep",
        top_k: int | None = None,
        search_id: str | None = None,
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

    async def query(self, request: KnowledgeQueryRequest) -> KnowledgeQueryResult:
        self._validate(request)
        strategy = self._resolve_strategy(request)
        if strategy == "recall":
            recall_kwargs = {
                "mode": request.mode,
                "top_k": request.top_k,
            }
            if request.correlation_id is not None:
                recall_kwargs["search_id"] = request.correlation_id
            recalled = await self._core.recall(request.query, **recall_kwargs)
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

        reflected = await self._core.reflect(
            request.query, mode=request.mode, top_k=request.top_k
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
            chunk_text=str(chunk.get("text") or item.source_text or item.text),
            score=item.final_score,
            metadata={
                **dict(item.metadata),
                "source_type": item.source_type,
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
        for memory_type, items in reflected.based_on.items():
            if memory_type in {"directives", "mental_models"}:
                continue
            for item in items:
                memory_id = str(item.get("id", ""))
                if not memory_id or memory_id in seen:
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


def build_query_service() -> HindsightQueryService:
    from config.settings import settings

    repository = PostgresMemoryRepository(
        keyword_index_enabled=settings.hindsight_keyword_index_enabled,
        keyword_candidate_limit=settings.hindsight_keyword_candidate_limit,
    )
    options = HindsightOptions(
        recall_min_semantic=settings.hindsight_recall_min_semantic,
        recall_min_score=settings.hindsight_recall_min_score,
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
