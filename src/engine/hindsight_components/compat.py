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
                memory_types=request.memory_types,
                source_types=request.source_types,
                tags=request.tags,
                tags_match=request.tags_match,
                reference_time=request.reference_time,
                min_scores=request.min_scores,
                prefer_observations=request.prefer_observations,
                include=request.include,
                include_stale=request.include_stale,
                timeout_seconds=request.timeout_seconds,
                max_tokens=request.max_tokens,
                max_candidates=request.max_candidates,
                route=request.route,
            )
        )

        document_sources = list(result.document_evidence)
        conversation_sources = list(result.conversation_context)
        if not document_sources and not conversation_sources:
            for source in result.sources:
                if (
                    source.source_group == "conversation_context"
                    or source.authority == "conversation"
                ):
                    conversation_sources.append(source)
                else:
                    document_sources.append(source)

        def to_chunk(source) -> RecallChunk:
            scores = source.metadata.get("scores", {})
            scores = scores if isinstance(scores, dict) else {}
            metadata = dict(source.metadata)
            metadata.setdefault("authority", source.authority)
            metadata.setdefault("source_group", source.source_group)
            return RecallChunk(
                doc_id=source.doc_id,
                title=source.title,
                chunk_text=source.chunk_text,
                reranker_score=source.score,
                vector_score=float(scores.get("semantic") or 0.0),
                memory_id=source.memory_id,
                memory_type=source.memory_type,
                metadata=metadata,
            )

        document_evidence = [to_chunk(source) for source in document_sources]
        conversation_context = [to_chunk(source) for source in conversation_sources]
        if request.route == "conversation":
            chunks = conversation_context
        elif request.route == "mixed":
            chunks = [*document_evidence, *conversation_context][: request.top_k]
        else:
            chunks = document_evidence

        related_docs: list[dict] = []
        seen_docs: set[str] = set()
        for source in document_sources:
            if source.doc_id and source.doc_id not in seen_docs:
                seen_docs.add(source.doc_id)
                # Hindsight 来源没有文档间关系类型；保持响应形状一致，
                # SPA 在 relation_type 为空时省略括号。
                related_docs.append(
                    {
                        "doc_id": source.doc_id,
                        "title": source.title,
                        "relation_type": "",
                        "reason": "",
                    }
                )

        return RecallResult(
            chunks=chunks,
            document_evidence=document_evidence,
            conversation_context=conversation_context,
            related_entities=list(result.related_entities),
            related_docs=related_docs,
            answer=result.answer,
            mode_used=mode,
            strategy_used=result.strategy_used,
            based_on=dict(result.based_on),
            trace={**dict(result.trace), "mode": mode},
        )
