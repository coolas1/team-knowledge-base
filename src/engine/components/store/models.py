import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    CheckConstraint,
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
