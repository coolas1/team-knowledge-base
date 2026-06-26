from contextlib import asynccontextmanager
from contextlib import AsyncExitStack

from fastapi import FastAPI

from src.api import mcp_server, routes
from src.core.knowledge_base import KnowledgeBase
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import init_db

neo4j_client: Neo4jClient | None = None
kb: KnowledgeBase | None = None
_exit_stack: AsyncExitStack | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_client, kb, _exit_stack
    # startup
    await init_db()
    neo4j_client = Neo4jClient()
    kb = KnowledgeBase(neo4j_client)
    routes.set_kb(kb)
    mcp_server.set_kb(kb)

    # 手动启动 MCP session manager 的 lifespan
    _exit_stack = AsyncExitStack()
    await _exit_stack.enter_async_context(mcp_server.mcp.session_manager.run())

    yield
    # shutdown
    await _exit_stack.aclose()
    await neo4j_client.close()


app = FastAPI(title="Team Knowledge Base", version="0.1.0", lifespan=lifespan)

# 注册 REST API 路由
app.include_router(routes.router)

# 挂载 MCP Server (streamable HTTP) — 不含 lifespan，由主 lifespan 管理
from starlette.routing import Mount
from mcp.server.fastmcp.server import StreamableHTTPASGIApp

# 触发 session_manager 懒加载
_standalone_mcp_app = mcp_server.mcp.streamable_http_app()

mcp_asgi = StreamableHTTPASGIApp(mcp_server.mcp.session_manager)
app.routes.append(Mount("/mcp", app=mcp_asgi))


@app.get("/health")
async def health():
    return {"status": "ok"}
