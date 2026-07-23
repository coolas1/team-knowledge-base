import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.config import settings

engine = create_async_engine(settings.postgres_dsn, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """启动时创建扩展并执行版本化 Alembic migrations。"""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(root / "alembic.ini"))
    expected_head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    async with engine.connect() as conn:
        has_version_table = await conn.scalar(text("SELECT to_regclass('public.alembic_version')"))
        current_revision = None
        if has_version_table:
            current_revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    if expected_head and current_revision == expected_head:
        return
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI Depends 注入用。"""
    async with async_session_factory() as session:
        yield session
