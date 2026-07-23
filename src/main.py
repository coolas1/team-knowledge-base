from contextlib import asynccontextmanager
from contextlib import AsyncExitStack
import logging

from fastapi import FastAPI
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from src.api import mcp_server, openwebui_server, routes
from src.core.knowledge_base import KnowledgeBase
from src.db.config import settings
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import init_db

neo4j_client: Neo4jClient | None = None
kb: KnowledgeBase | None = None
_exit_stack: AsyncExitStack | None = None
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_client, kb, _exit_stack
    # startup
    logger.info("Initializing PostgreSQL and migrations")
    await init_db()
    logger.info("Initializing Neo4j")
    neo4j_client = Neo4jClient()
    await neo4j_client.initialize(settings.default_team_id)
    logger.info("Starting knowledge-base workers")
    kb = KnowledgeBase(neo4j_client)
    routes.set_kb(kb)
    mcp_server.set_kb(kb)
    openwebui_server.set_kb(kb)
    await kb.start()

    # 手动启动 MCP session manager 的 lifespan
    _exit_stack = AsyncExitStack()
    await _exit_stack.enter_async_context(mcp_server.mcp.session_manager.run())
    logger.info("TKB startup complete")

    yield
    # shutdown
    await _exit_stack.aclose()
    await kb.stop()
    await neo4j_client.close()


app = FastAPI(title="Team Knowledge Base", version="0.1.0", lifespan=lifespan)

# 注册 REST API 路由
app.include_router(routes.router)

# Open WebUI v0.10 accepts OpenAPI tool servers rather than MCP directly.
openwebui_app = FastAPI(
    title="TKB Open WebUI Tools",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
)
openwebui_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
openwebui_app.include_router(openwebui_server.router)
app.mount("/openwebui", openwebui_app)

# 挂载 MCP Server (streamable HTTP) — 不含 lifespan，由主 lifespan 管理
# 触发 session_manager 懒加载
_standalone_mcp_app = mcp_server.mcp.streamable_http_app()

mcp_asgi = StreamableHTTPASGIApp(mcp_server.mcp.session_manager)
app.routes.append(Mount("/mcp", app=mcp_asgi))


@app.get("/health")
async def health():
    return {"status": "ok"}
