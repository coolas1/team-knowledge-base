import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.engine.components.store.ownership import BankOwned
from src.engine.scope import DEFAULT_BANK_ID, MemoryScope

EMBEDDING_DIM = 768  # nomic-embed-text, 可通过配置切换
INTERNAL_DOCUMENT_FILE_TYPES = frozenset({"conversation"})


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryBank(Base):
    __tablename__ = "memory_banks"
    __table_args__ = (
        CheckConstraint("policy_version > 0", name="ck_memory_bank_policy_version"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )


class Document(BankOwned, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # markdown|pdf|docx|pptx|image|...
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overview: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )  # LLM 生成的索引摘要
    file_path: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # 原始文件本地路径
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # SHA256
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )  # pending → processing → indexed / failed
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_generation: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # ── 版本链（纵向迭代管理）───────────────────────────────────
    # 同一逻辑文档的多个版本共享 version_group；每行是组内一个具体版本。
    # version_of 指向上一版 doc_id；is_current 标记组内最新版（默认检索域）。
    version_group: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
    )
    version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    version_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=False,
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

    __table_args__ = (
        Index("idx_documents_status", "status"),
        Index("idx_documents_type", "file_type"),
        Index("idx_documents_version_group", "version_group"),
        Index("idx_documents_is_current", "is_current"),
        UniqueConstraint(
            "version_group", "version_number", name="uq_documents_group_version"
        ),
        Index(
            "uq_documents_current_version",
            "version_group",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )


def public_document_filter(scope: MemoryScope | None = None):
    """SQL predicate shared by APIs that expose user-uploaded documents."""

    from sqlalchemy import and_
    from src.engine.components.store.scope import scope_predicate

    return and_(
        Document.file_type.not_in(INTERNAL_DOCUMENT_FILE_TYPES),
        scope_predicate(Document.bank_id, Document.tags, scope or MemoryScope()),
    )


def is_public_document(document: Document, scope: MemoryScope | None = None) -> bool:
    return document.file_type not in INTERNAL_DOCUMENT_FILE_TYPES and (
        scope or MemoryScope()
    ).permits(
        getattr(document, "bank_id", None) or DEFAULT_BANK_ID,
        getattr(document, "tags", None) or [],
    )


class Chunk(BankOwned, Base):
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
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    overview: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )  # 冗余自 documents.overview
    doc_uri: Mapped[str] = mapped_column(Text, nullable=False)  # doc_id:标题
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    # relationships
    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_chunks_doc_chunk_index"),
    )


class DocumentRetrieval(BankOwned, Base):
    """Clean, revision-fenced parent representation for document retrieval."""

    __tablename__ = "document_retrieval"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    entities: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    field_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    generation_state: Mapped[str] = mapped_column(
        Text, nullable=False, default="ready", server_default="ready"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_document_retrieval_revision"),
        CheckConstraint(
            "generation_state IN ('pending', 'ready', 'failed')",
            name="ck_document_retrieval_generation_state",
        ),
        Index("idx_document_retrieval_bank", "bank_id"),
        Index("idx_document_retrieval_tokens", "field_tokens", postgresql_using="gin"),
    )


class ArchiveJob(Base):
    """自动归档持久队列：inbox 中发现的每个稳定文件一行。

    状态机: queued -> processing -> awaiting_review / done / failed；用户暂不
    归档后为 unarchived。skipped 仅为 V1 历史兼容状态。
    content_hash 是去重键：已存在同 hash 的 job 时扫描器不再入队。
    """

    __tablename__ = "archive_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()")
    )
    plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    routing_reason: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        onupdate=_utcnow,
    )

    __table_args__ = (
        Index("idx_archive_jobs_status", "status"),
        Index("idx_archive_jobs_hash", "content_hash"),
    )


class ArchiveOperation(Base):
    """归档操作日志（撤销依据）：每次执行的 move 一行。"""

    __tablename__ = "archive_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("archive_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    destination_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    decision_source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("archive_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kb_doc_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="executed", server_default="executed"
    )
    undo_status: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        onupdate=_utcnow,
    )

    __table_args__ = (Index("idx_archive_operations_job", "job_id"),)


class ArchivePolicy(Base):
    """可编辑且版本化的归档规则；任一时刻至多一个 active 版本。"""

    __tablename__ = "archive_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("version", name="uq_archive_policy_version"),
        Index(
            "uq_archive_policy_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class ArchiveMigrationBatch(Base):
    """存量文件归档的可审计批次：扫描/预览结果与逐项执行结果。"""

    __tablename__ = "archive_migration_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("archive_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="planned", server_default="planned"
    )
    plan: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        onupdate=_utcnow,
    )


class DocumentChange(Base):
    """两个相邻版本间的结构化变更记录（LLM diff 产物）。

    doc_id 指向新版本（to_version）所属的 documents 行；from_version
    是它的上一版版本号。changes 是 VersionRAG 风格的变更条目数组。
    """

    __tablename__ = "document_changes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    changes: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    document: Mapped["Document"] = relationship()

    __table_args__ = (
        UniqueConstraint("doc_id", "from_version", name="uq_document_changes_pair"),
        Index("idx_document_changes_doc", "doc_id"),
    )
