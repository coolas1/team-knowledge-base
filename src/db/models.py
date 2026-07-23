import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 768  # nomic-embed-text, 可通过配置切换


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )


class TeamApiToken(Base):
    """Database-backed bearer token managed by a team administrator."""

    __tablename__ = "team_api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    roles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: ["member"], server_default=text("'[\"member\"]'::jsonb")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        Index("idx_team_api_tokens_team_created", "team_id", "created_at"),
        Index("idx_team_api_tokens_hash_active", "token_hash", "active"),
    )


class TrustedOllamaAccount(Base):
    """An administrator-approved Ollama username membership for one team."""

    __tablename__ = "trusted_ollama_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    roles: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=lambda: ["viewer"], server_default=text("'[\"viewer\"]'::jsonb")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"),
        onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("team_id", "username", name="uq_trusted_ollama_team_username"),
        Index("idx_trusted_ollama_username_active", "username", "active"),
        Index("idx_trusted_ollama_team_created", "team_id", "created_at"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("teams.id"), nullable=False, default="default", server_default="default"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(Text, nullable=False)  # markdown|pdf|docx|pptx|image|...
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    overview: Mapped[str] = mapped_column(Text, nullable=False, default="")  # LLM 生成的索引摘要
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 原始文件本地路径
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # SHA256
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )  # pending → processing → indexed / failed
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="team", server_default="team")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    graph_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
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

    __table_args__ = (
        Index("idx_documents_status", "status"),
        Index("idx_documents_type", "file_type"),
        Index("idx_documents_team_status", "team_id", "status"),
        Index("idx_documents_team_scope", "team_id", "scope"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("teams.id"), nullable=False, default="default", server_default="default"
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
        Index("idx_chunks_team_doc", "team_id", "doc_id"),
    )


class ExtractedEntity(Base):
    """PostgreSQL 中的权威实体事实；Neo4j 仅保存其投影。"""

    __tablename__ = "extracted_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "team_id", "doc_id", "document_version", "chunk_index", "normalized_name", "entity_type",
            name="uq_extracted_entity_source",
        ),
        Index("idx_extracted_entities_projection", "team_id", "doc_id", "document_version"),
    )


class ExtractedRelation(Base):
    """PostgreSQL 中的权威关系事实。"""

    __tablename__ = "extracted_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    from_name: Mapped[str] = mapped_column(Text, nullable=False)
    to_name: Mapped[str] = mapped_column(Text, nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "team_id", "doc_id", "document_version", "chunk_index", "from_name", "to_name", "relation_type",
            name="uq_extracted_relation_source",
        ),
        Index("idx_extracted_relations_projection", "team_id", "doc_id", "document_version"),
    )


class DocumentRelation(Base):
    __tablename__ = "document_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    source_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    target_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("team_id", "source_doc_id", "target_doc_id", "relation_type", name="uq_document_relation"),
    )


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("team_id", "idempotency_key", name="uq_operation_idempotency"),
        Index("idx_operations_claim", "status", "next_retry_at", "lease_expires_at"),
        Index("idx_operations_team_created", "team_id", "created_at"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[str] = mapped_column(String(128), ForeignKey("teams.id"), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=text("now()"), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "team_id", "aggregate_type", "aggregate_id", "aggregate_version", "event_type",
            name="uq_outbox_aggregate_version",
        ),
        Index("idx_outbox_claim", "status", "next_retry_at", "lease_expires_at"),
    )
