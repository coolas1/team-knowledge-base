from contextlib import asynccontextmanager
from contextlib import AsyncExitStack
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api import mcp_server, routes
from src.core.bm25_index import bm25_index
from src.core.knowledge_base import KnowledgeBase
from src.core.log_manager import setup_logging, shutdown_logging
from src.core.version_manager import VersionManager
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import init_db, migrate_legacy_documents, async_session_factory
from src.watcher.config import load_watch_config
from src.watcher.watcher import FileWatcher
from src.watcher.scheduler import PipelineScheduler

logger = logging.getLogger(__name__)

neo4j_client: Neo4jClient | None = None
kb: KnowledgeBase | None = None
_exit_stack: AsyncExitStack | None = None
_file_watcher: FileWatcher | None = None
_scheduler: PipelineScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global neo4j_client, kb, _exit_stack, _file_watcher, _scheduler
    # startup
    setup_logging()
    await init_db()

    # 存量数据迁移
    await migrate_legacy_documents()

    neo4j_client = Neo4jClient()
    kb = KnowledgeBase(neo4j_client)
    routes.set_kb(kb)
    mcp_server.set_kb(kb)

    # 版本管理器
    version_manager = VersionManager(pipeline=kb._pipeline)
    routes.set_version_manager(version_manager)

    # 目录监控 + Pipeline 调度
    watch_config = load_watch_config()
    if watch_config.enabled:
        _file_watcher = FileWatcher(watch_config)
        await _file_watcher.start()
        logger.info("目录监控已启动")

    _scheduler = PipelineScheduler(kb, watch_config)
    routes.set_scheduler(_scheduler)
    await _scheduler.start()

    # 手动启动 MCP session manager 的 lifespan
    _exit_stack = AsyncExitStack()
    await _exit_stack.enter_async_context(mcp_server.mcp.session_manager.run())

    # 构建 BM25 索引（从 PostgreSQL 加载所有 indexed chunks）
    try:
        async with async_session_factory() as bm25_session:
            await bm25_index.build_from_db(bm25_session)
        logger.info("BM25 索引构建完成")
    except Exception as e:
        logger.warning(f"BM25 索引构建失败，关键词检索不可用: {e}")

    yield
    # shutdown
    if _scheduler:
        await _scheduler.stop()
    if _file_watcher:
        await _file_watcher.stop()
    await shutdown_logging()
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


@app.exception_handler(Exception)
async def _debug_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    detail = "".join(tb)
    logger.error(f"Unhandled exception: {detail}")
    return JSONResponse(status_code=500, content={"detail": detail})


@app.get("/health")
async def health():
    return {"status": "ok"}
