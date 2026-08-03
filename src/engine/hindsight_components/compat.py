"""Compatibility adapter from Hindsight queries to the original recall API."""

from __future__ import annotations

from typing import Literal

from src.engine.interface import (
    KnowledgeQuery,
    KnowledgeQueryRequest,
    RecallChunk,
    RecallRequest,
    RecallResult,
)

_DEEP_QUERY_MARKERS = (
    "为什么",
    "原因",
    "如何",
    "分析",
    "比较",
    "对比",
    "总结",
    "归纳",
    "关系",
    "影响",
    "趋势",
    "时间线",
    "跨文档",
    "why",
    "how",
    "analyze",
    "compare",
    "summarize",
    "relationship",
    "timeline",
    "across documents",
)


def resolve_recall_mode(
    query: str,
    mode: Literal["auto", "fast", "deep"],
    *,
    needs_answer: bool,
) -> Literal["fast", "deep"]:
    """Resolve the backwards-compatible ``auto`` mode without an extra LLM call.

    MCP callers can still choose fast/deep explicitly.  For legacy callers that
    know neither option, answer synthesis and clearly analytical queries prefer
    deep retrieval; short fact lookups stay fast.
    """

    if mode not in {"auto", "fast", "deep"}:
        raise ValueError(f"unsupported retrieval mode: {mode}")
    if mode != "auto":
        return mode
    normalized = " ".join(query.casefold().split())
    if needs_answer or len(normalized) >= 80:
        return "deep"
    if any(marker in normalized for marker in _DEEP_QUERY_MARKERS):
        return "deep"
    return "fast"


class HindsightRecallAdapter:
    """Expose a Hindsight ``KnowledgeQuery`` through the original recall DTOs."""

    def __init__(self, query_service: KnowledgeQuery) -> None:
        self._query_service = query_service

    async def recall(self, request: RecallRequest) -> RecallResult:
        if not request.query.strip():
            raise ValueError("query cannot be empty")
        if request.top_k < 1:
            raise ValueError("top_k must be greater than zero")
        mode = resolve_recall_mode(
            request.query,
            request.mode,
            needs_answer=request.needs_answer,
        )
        result = await self._query_service.query(
            KnowledgeQueryRequest(
                query=request.query,
                strategy="auto",
                mode=mode,
                top_k=request.top_k,
                needs_answer=request.needs_answer,
            )
        )

        related_docs: list[dict] = []
        seen_docs: set[str] = set()
        chunks: list[RecallChunk] = []
        for source in result.sources:
            scores = source.metadata.get("scores", {})
            scores = scores if isinstance(scores, dict) else {}
            chunks.append(
                RecallChunk(
                    doc_id=source.doc_id,
                    title=source.title,
                    chunk_text=source.chunk_text,
                    reranker_score=source.score,
                    vector_score=float(scores.get("semantic") or 0.0),
                    memory_id=source.memory_id,
                    memory_type=source.memory_type,
                    metadata=dict(source.metadata),
                )
            )
            if source.doc_id and source.doc_id not in seen_docs:
                seen_docs.add(source.doc_id)
                related_docs.append({"doc_id": source.doc_id, "title": source.title})

        return RecallResult(
            chunks=chunks,
            related_entities=list(result.related_entities),
            related_docs=related_docs,
            answer=result.answer,
            mode_used=mode,
            strategy_used=result.strategy_used,
            based_on=dict(result.based_on),
            trace={**dict(result.trace), "mode": mode},
        )
