"""BFF graph routes: full graph / entity / neighbors."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("/full")
async def full_graph(engine: EngineClient = Depends(deps.get_engine)):
    return await engine.get_graph(None)


@router.get("/entity/{name}")
async def entity_graph(name: str, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.get_graph(name)


@router.get("/neighbors/{name}")
async def neighbors(
    name: str,
    hops: int = Query(2, ge=1, le=3),
    engine: EngineClient = Depends(deps.get_engine),
):
    return await engine.get_neighbors(name)
