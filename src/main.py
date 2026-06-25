from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import mcp_server, routes
from src.core.knowledge_base import KnowledgeBase
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import init_db

neo4j_client: Neo4jClient | None = None
kb: KnowledgeBase | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_client, kb
    # startup
    await init_db()
    neo4j_client = Neo4jClient()
    kb = KnowledgeBase(neo4j_client)
    routes.set_kb(kb)
    mcp_server.set_kb(kb)
    yield
    # shutdown
    await neo4j_client.close()


app = FastAPI(title="Team Knowledge Base", version="0.1.0", lifespan=lifespan)

# 注册 REST API 路由
app.include_router(routes.router)

# 挂载 MCP Server (streamable HTTP)
mcp_app = mcp_server.mcp.streamable_http_app()
app.mount("/mcp", mcp_app)


@app.get("/health")
async def health():
    return {"status": "ok"}
