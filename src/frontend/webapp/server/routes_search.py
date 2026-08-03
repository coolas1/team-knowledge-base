"""BFF search route."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=20, ge=1)
    mode: Literal["auto", "fast", "deep"] = "auto"
    needs_answer: bool = False


@router.post("/search")
async def search(body: SearchRequest, engine: EngineClient = Depends(deps.get_engine)):
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="query cannot be empty")
    try:
        return await engine.recall(
            body.query,
            top_k=body.top_k,
            mode=body.mode,
            needs_answer=body.needs_answer,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
