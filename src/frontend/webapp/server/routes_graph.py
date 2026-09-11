"""BFF graph routes: full graph / entity / neighbors (KnowledgeBase direct)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from src.engine.interface import KnowledgeBase
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/full")
async def full_graph(kb: KnowledgeBase = Depends(deps.get_kb)):
    return asdict(await kb.get_graph(None))


@router.get("/entity/{name}")
async def entity_graph(name: str, kb: KnowledgeBase = Depends(deps.get_kb)):
    return asdict(await kb.get_graph(name))


@router.get("/neighbors/{name}")
async def neighbors(
    name: str,
    hops: int = Query(2, ge=1, le=3),
    kb: KnowledgeBase = Depends(deps.get_kb),
):
    return asdict(await kb.get_neighbors(name, hops))
