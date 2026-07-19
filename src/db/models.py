import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024  # text-embedding-v3 (DashScope), 可通过配置切换


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(Text, nullable=False)  # markdown|pdf|docx|pptx|image|...
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")  # LLM 生成的索引摘要
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 原始文件本地路径
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # SHA256

    # ── 版本管理新增字段 ─────────────────────────────────────────
    source_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )  # 'manual' | 'watch'
    source_path: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 监控目录下的相对路径，手动上传为 NULL
    watch_dir: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 所属监控目录绝对路径，手动上传为 NULL

    # ── 正交状态模型 ─────────────────────────────────────────────
    index_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )  # pending → processing → indexed / failed / stale
    file_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )  # active | disappeared

    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    # relationships
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentVersion.version.desc()",
    )

    __table_args__ = (
        Index("idx_documents_index_status", "index_status"),
        Index("idx_documents_file_status", "file_status"),
        Index("idx_documents_type", "file_type"),
        Index("idx_documents_source_path", "source_path"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")  # 冗余自 documents.overview
    doc_uri: Mapped[str] = mapped_column(Text, nullable=False)  # doc_id:标题
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )

    # relationships
    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_chunks_doc_chunk_index"),
    )


class DocumentVersion(Base):
    """文档版本记录，支持 diff 对比和历史回溯。"""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)  # 从 1 递增
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)  # 提取后的纯文本（用于 diff）
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # SHA256
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 磁盘快照路径
    change_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="create"
    )  # create | modify | rename | rollback
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # 如 "+12 -3"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )

    # relationships
    document: Mapped["Document"] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("doc_id", "version", name="uq_document_versions_doc_version"),
        Index("idx_versions_doc_id", "doc_id"),
    )


class LogEntry(Base):
    """系统日志条目，支持按时间/模块/文档ID/trace_id查询。"""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )
    level: Mapped[str] = mapped_column(Text, nullable=False)  # DEBUG|INFO|WARNING|ERROR|CRITICAL
    module: Mapped[str] = mapped_column(Text, nullable=False)  # logger 名称，如 src.pipeline.pipeline
    message: Mapped[str] = mapped_column(Text, nullable=False)
    doc_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # 关联文档 UUID（从消息中提取）
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # pipeline 执行追踪 ID
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 扩展字段（堆栈等）

    __table_args__ = (
        Index("idx_logs_timestamp", "timestamp"),
        Index("idx_logs_level", "level"),
        Index("idx_logs_doc_id", "doc_id"),
        Index("idx_logs_trace_id", "trace_id"),
    )
