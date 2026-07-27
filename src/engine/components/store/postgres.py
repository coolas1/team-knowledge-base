from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import settings
from src.engine.components.store.models import Base

engine = create_async_engine(settings.postgres_dsn, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """启动时调用：创建扩展 + 建表 + 创建特殊索引。"""
    async with engine.begin() as conn:
        # 1. 创建 pgvector + pg_trgm 扩展
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # 2. 建表（幂等）
        await conn.run_sync(Base.metadata.create_all)

        # 3. 创建特殊索引（SQLAlchemy DDL 不支持这些 PostgreSQL 特有索引）
        # title 模糊搜索索引
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_documents_title_trgm "
            "ON documents USING gin(title gin_trgm_ops)"
        ))
        # HNSW 向量索引
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_chunks_embedding "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        ))


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI Depends 注入用。"""
    async with async_session_factory() as session:
        yield session
