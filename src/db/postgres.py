import logging

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.config import settings
from src.db.models import Base, Document, DocumentVersion

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.postgres_dsn, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """启动时调用：创建扩展 + 建表 + 添加新列 + 创建特殊索引。"""
    async with engine.begin() as conn:
        # 1. 创建 pgvector + pg_trgm 扩展
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

        # 2. 建表（幂等）
        await conn.run_sync(Base.metadata.create_all)

        # 3. 为已有的 documents 表添加新列（幂等，IF NOT EXISTS）
        alter_stmts = [
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'manual'",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS watch_dir TEXT",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_status TEXT NOT NULL DEFAULT 'pending'",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_status TEXT NOT NULL DEFAULT 'active'",
        ]
        for stmt in alter_stmts:
            await conn.execute(text(stmt))

        # 4. 迁移旧 status 列数据到 index_status（如果 status 列还存在）
        result = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'status'"
        ))
        if result.fetchone():
            await conn.execute(text(
                "UPDATE documents SET index_status = status "
                "WHERE status IS NOT NULL AND status != 'pending'"
            ))
            # 可选：删除旧列（这里保留以避免意外，可以在确认无误后手动删除）
            # await conn.execute(text("ALTER TABLE documents DROP COLUMN status"))

        # 5. 为已有的 logs 表添加新列（幂等，IF NOT EXISTS）
        log_alter_stmts = [
            "ALTER TABLE logs ADD COLUMN IF NOT EXISTS trace_id TEXT",
            "ALTER TABLE logs ADD COLUMN IF NOT EXISTS doc_id TEXT",
            "ALTER TABLE logs ADD COLUMN IF NOT EXISTS extra JSONB",
        ]
        for stmt in log_alter_stmts:
            await conn.execute(text(stmt))

        # 6. 创建特殊索引（SQLAlchemy DDL 不支持这些 PostgreSQL 特有索引）
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


async def migrate_legacy_documents() -> None:
    """将老数据迁移到版本管理模型。

    在服务启动时调用，幂等操作：
    1. 为无 source_type 的文档设置 source_type='manual'
    2. 将旧 status 列的值迁移到 index_status（如果 status 列存在）
    3. 设置 file_status='active'
    4. 为每个无版本记录的文档创建 version=1
    """
    async with async_session_factory() as session:
        # 检查是否需要迁移（通过检查是否存在旧 status 列）
        result = await session.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'documents' AND column_name = 'status'"
        ))
        has_old_status = result.fetchone() is not None

        if has_old_status:
            # 将旧 status 列的值迁移到 index_status
            await session.execute(text(
                "UPDATE documents SET index_status = status "
                "WHERE index_status = 'pending' AND status != 'pending'"
            ))
            logger.info("已将旧 status 列迁移到 index_status")

        # 确保所有文档都有 source_type
        await session.execute(text(
            "UPDATE documents SET source_type = 'manual', file_status = 'active' "
            "WHERE source_type IS NULL OR source_type = ''"
        ))

        # 为没有版本记录的文档创建 version=1
        docs_without_versions = await session.execute(
            select(Document.id, Document.raw_text, Document.content_hash, Document.title)
            .where(
                ~Document.id.in_(
                    select(DocumentVersion.doc_id).distinct()
                )
            )
        )
        docs = docs_without_versions.all()
        for doc_id, raw_text, content_hash, title in docs:
            if not content_hash:
                import hashlib
                content_hash = hashlib.sha256((raw_text or "").encode()).hexdigest()
            version = DocumentVersion(
                doc_id=doc_id,
                version=1,
                raw_text=raw_text or "",
                content_hash=content_hash,
                change_type="create",
            )
            session.add(version)

        if docs:
            logger.info(f"已为 {len(docs)} 个老文档创建 version=1 记录")

        await session.commit()
