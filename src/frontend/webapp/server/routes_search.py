"""BFF search route: call KnowledgeBase directly. When a KnowledgeQuery is
wired (hindsight engine), search delegates to the HindsightRecallAdapter for
mode/needs_answer support; otherwise it falls back to plain GraphRAG recall."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.engine.interface import KnowledgeBase, KnowledgeQuery, RecallRequest
from src.frontend.webapp.server import deps

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20
    mode: Literal["auto", "fast", "deep"] = "auto"
    needs_answer: bool = False
    memory_types: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tags_match: str = "any"
    reference_time: datetime | None = None
    min_scores: dict[str, float] = Field(default_factory=dict)
    prefer_observations: bool = False
    include: list[str] = Field(default_factory=lambda: ["chunks", "entities"])
    include_stale: bool = False
    timeout_seconds: float | None = None
    max_tokens: int | None = None
    max_candidates: int | None = None

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be empty")
        return value


@router.post("/search")
async def search(
    body: SearchRequest,
    kb: KnowledgeBase = Depends(deps.get_kb),
    query_service: KnowledgeQuery | None = Depends(deps.get_query),
):
    request = RecallRequest(
        query=body.query,
        top_k=body.top_k,
        mode=body.mode,
        needs_answer=body.needs_answer,
        memory_types=tuple(body.memory_types),
        source_types=tuple(body.source_types),
        tags=tuple(body.tags),
        tags_match=body.tags_match,
        reference_time=body.reference_time,
        min_scores=dict(body.min_scores),
        prefer_observations=body.prefer_observations,
        include=tuple(body.include),
        include_stale=body.include_stale,
        timeout_seconds=body.timeout_seconds,
        max_tokens=body.max_tokens,
        max_candidates=body.max_candidates,
    )
    if query_service is not None:
        from src.engine.hindsight_components.compat import HindsightRecallAdapter

        result = await HindsightRecallAdapter(query_service).recall(request)
    else:
        extended = body.model_dump(exclude={"query", "top_k", "mode", "needs_answer"})
        defaults = SearchRequest(query=body.query).model_dump(
            exclude={"query", "top_k", "mode", "needs_answer"}
        )
        if body.mode != "auto" or body.needs_answer or extended != defaults:
            raise HTTPException(503, "Hindsight 查询服务未初始化")
        result = await kb.recall(request)

    payload = asdict(result)
    for chunk in payload.get("chunks", []):
        chunk["chunk_text"] = chunk["chunk_text"][:1000]
    return payload
