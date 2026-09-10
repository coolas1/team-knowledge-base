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
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

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


# A stale cached shell can reference a hashed bundle a later deploy removed.
# Answering that request with index.html would hand the browser HTML where it
# asked for JavaScript, and it fails with a confusing parse error; a bare JSON
# 404 leaves a blank page. Both cases get a readable recovery document instead
# (the SPA shell's own pre-bootstrap guard covers the other ordering, where the
# shell itself is the stale thing).
_ASSET_MISS_BODY = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>页面资源不可用</title>
    <style>
      body { margin: 0; padding: 32px; box-sizing: border-box;
        font: 14px/1.6 system-ui, -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif;
        background: #f5f7f8; color: #1f2933; text-align: center; }
      .box { max-width: 32em; margin: 15vh auto 0; }
      h1 { font-size: 18px; margin: 0 0 12px; }
      p { color: #52606d; margin: 0 0 20px; }
      button { padding: 7px 20px; border: 1px solid #1f2933; border-radius: 5px;
        background: transparent; color: #1f2933; font-size: 14px; cursor: pointer; }
    </style>
  </head>
  <body>
    <div class="box">
      <h1>页面资源不可用</h1>
      <p>应用资源可能已被更新或暂时无法获取，请刷新页面重试。</p>
      <button type="button" onclick="window.location.reload()">刷新页面</button>
    </div>
  </body>
</html>
"""

# Built SPA static assets, when present. ASSETS_ROUTE is the single source of
# the mount path, so the fallback below cannot drift from what is mounted.
ASSETS_ROUTE = "assets"
_assets = SPA_DIST / ASSETS_ROUTE


class _AssetFiles(StaticFiles):
    """Static files whose misses answer with the recovery document.

    The default raises an HTTPException, which FastAPI renders as a bare JSON
    404 — the wrong content type for a request that asked for JavaScript. The
    file-hit path is untouched, so ETag/Last-Modified caching still applies.

    Catch the Starlette base class, not FastAPI's: StaticFiles raises the
    former, and FastAPI's is a *subclass*, so ``except HTTPException`` alone
    would not catch it.
    """

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return PlainTextResponse(
                    _ASSET_MISS_BODY, status_code=404, media_type="text/html"
                )
            raise


if _assets.is_dir():
    app.mount(f"/{ASSETS_ROUTE}", _AssetFiles(directory=str(_assets)), name="spa-assets")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serve the SPA shell for any non-API GET (client-side routing).

    API misses, /health, and /mcp stay 404 so they are not masked by
    index.html (the MCP endpoint must answer JSON, never the SPA shell).

    Static-asset misses are carved out too: a request under the built
    ``assets/`` directory is never a client-side route, so it must not be
    answered with the shell.
    """
    if (
        full_path == "health"
        or full_path == "mcp"
        or full_path.startswith("api/")
        or full_path.startswith("mcp/")
    ):
        raise HTTPException(status_code=404, detail="Not Found")
    if full_path.startswith(ASSETS_ROUTE + "/"):
        return PlainTextResponse(
            _ASSET_MISS_BODY, status_code=404, media_type="text/html"
        )
    index = SPA_DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="SPA not built")
