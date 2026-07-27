"""Webapp BFF (FastAPI). Calls the engine via EngineClient and may invoke
agent skills in-process. Lifespan wires the engine + agent plugin.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.frontend.webapp.server import deps
from src.frontend.webapp.server.routes_documents import router as documents_router
from src.frontend.webapp.server.routes_search import router as search_router
from src.frontend.webapp.server.routes_graph import router as graph_router
from src.frontend.webapp.server.routes_agent import router as agent_router
from src.frontend.webapp.server.routes_config import router as config_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await deps.startup()
    yield
    await deps.shutdown()


app = FastAPI(title="Team Knowledge Base BFF", version="0.1.0", lifespan=lifespan)

app.include_router(documents_router)
app.include_router(search_router)
app.include_router(graph_router)
app.include_router(agent_router)
app.include_router(config_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
