"""BFF search route."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 20


@router.post("/search")
async def search(body: SearchRequest, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.recall(body.query, top_k=body.top_k)
