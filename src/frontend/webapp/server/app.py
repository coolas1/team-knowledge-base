"""Webapp BFF (FastAPI). Calls the engine via EngineClient and may invoke
agent skills in-process. Lifespan wires the engine + agent plugin.

Routing: API endpoints live under ``/api`` so they never collide with the
SPA's client-side routes (``/``, ``/documents/:id``, ``/search``, ...). Any
other GET falls through to the built SPA (``index.html``); in dev the SPA is
served by Vite and only ``/api/*`` is proxied here.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from src.engine.mcp import build_app as build_mcp_app
from src.engine.mcp import mcp as mcp_server
from src.frontend.webapp.server import deps
from src.frontend.webapp.server.routes_documents import router as documents_router
from src.frontend.webapp.server.routes_search import router as search_router
from src.frontend.webapp.server.routes_graph import router as graph_router
from src.frontend.webapp.server.routes_query import router as query_router
from src.frontend.webapp.server.routes_agent import router as agent_router
from src.frontend.webapp.server.routes_artifacts import router as artifacts_router
from src.frontend.webapp.server.routes_config import router as config_router

# Where the built SPA (vite build output) lives. Set SPA_DIST in the container;
# in dev this path is absent and the SPA is served by Vite instead.
SPA_DIST = Path(os.getenv("SPA_DIST", "src/frontend/webapp/client/dist"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await deps.startup()
    try:
        if deps.engine_initialized():
            async with mcp_server.session_manager.run():
                yield
        else:
            # Unit tests replace startup with a no-op and exercise REST only.
            yield
    finally:
        await deps.shutdown()


app = FastAPI(title="Team Knowledge Base BFF", version="0.1.0", lifespan=lifespan)

# API under /api (see module docstring).
api = APIRouter(prefix="/api")
api.include_router(documents_router)
api.include_router(search_router)
api.include_router(query_router)
api.include_router(graph_router)
api.include_router(agent_router)
api.include_router(artifacts_router)
api.include_router(config_router)
app.include_router(api)
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
