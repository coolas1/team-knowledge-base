"""BFF search route: call KnowledgeBase directly. When a KnowledgeQuery is
wired (hindsight engine), search delegates to the HindsightRecallAdapter for
mode/needs_answer support; otherwise it falls back to plain GraphRAG recall."""
from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from src.engine.interface import KnowledgeBase, KnowledgeQuery, RecallRequest
from src.frontend.webapp.server import deps

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20
    mode: Literal["auto", "fast", "deep"] = "auto"
    needs_answer: bool = False

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
    )
    if query_service is not None:
        from src.engine.hindsight_components.compat import HindsightRecallAdapter

        result = await HindsightRecallAdapter(query_service).recall(request)
    else:
        if body.mode != "auto" or body.needs_answer:
            raise HTTPException(503, "Hindsight 查询服务未初始化")
        result = await kb.recall(request)

    payload = asdict(result)
    for chunk in payload.get("chunks", []):
        chunk["chunk_text"] = chunk["chunk_text"][:1000]
    return payload
