"""Webapp BFF (FastAPI). Single-app wiring: engine, plugin, and MCP all run
in-process; the BFF serves the SPA, the REST API, and /mcp.

Routing: API endpoints live under ``/api`` so they never collide with the
SPA's client-side routes. Any other GET falls through to the built SPA.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from src.frontend.webapp.server import deps
from src.frontend.webapp.server.routes_documents import router as documents_router
from src.frontend.webapp.server.routes_search import router as search_router
from src.frontend.webapp.server.routes_graph import router as graph_router
from src.frontend.webapp.server.routes_query import router as query_router
from src.frontend.webapp.server.routes_agent import router as agent_router
from src.frontend.webapp.server.routes_artifacts import router as artifacts_router
from src.frontend.webapp.server.routes_config import router as config_router
from src.agent.tkb.mcp.server import build_app as build_mcp_app

SPA_DIST = Path(os.getenv("SPA_DIST", "src/frontend/webapp/client/dist"))

# The FastMCP session manager allows a single run() per instance; the app has
# one lifespan per process. Tests open many TestClients against this app, so
# guard against repeated runs (later test lifespans skip it).
_mcp_session_started = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_session_started
    await deps.startup()
    from src.agent.tkb.mcp.server import mcp as mcp_server

    try:
        if _mcp_session_started:
            yield
            return
        _mcp_session_started = True
        async with mcp_server.session_manager.run():
            yield
    finally:
        await deps.shutdown()


app = FastAPI(title="Team Knowledge Base BFF", version="0.1.0", lifespan=lifespan)

# API under /api.
api = APIRouter(prefix="/api")
api.include_router(documents_router)
api.include_router(search_router)
api.include_router(query_router)
api.include_router(graph_router)
api.include_router(agent_router)
api.include_router(artifacts_router)
api.include_router(config_router)
app.include_router(api)

# MCP: always mounted in-process (single-app wiring).
app.mount("/mcp", build_mcp_app(), name="mcp")


@app.get("/health")
async def health():
    return {"status": "ok"}


# Built SPA static assets, when present.
_assets = SPA_DIST / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="spa-assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serve the SPA shell for any non-API GET (client-side routing).

    API misses and /health stay 404 so they are not masked by index.html.
    """
    if full_path == "health" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    index = SPA_DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="SPA not built")
