"""SQLAlchemy models for Hindsight-specific memory state.

The models share the existing project's ``Base`` and reference its
``documents`` table.  They deliberately do not add another Document model or
relationships back onto the existing GraphRAG ORM classes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.engine.components.store.models import EMBEDDING_DIM, Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryUnit(Base):
    __tablename__ = "memory_units"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_index: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False, default="world")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    occurred_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurred_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mentioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    is_source_chunk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    location: Mapped[str | None] = mapped_column(Text)
    proof_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            "memory_index",
            name="uq_memory_source_index",
        ),
        Index("idx_memory_units_document", "document_id"),
        Index("idx_memory_units_type", "memory_type"),
        Index("idx_memory_units_state", "state"),
        Index("idx_memory_units_occurred_start", "occurred_start"),
        Index("idx_memory_units_occurred_end", "occurred_end"),
    )


class MemoryEntity(Base):
    __tablename__ = "memory_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False, default="Entity")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        nullable=False,
    )

    __table_args__ = (Index("idx_memory_entities_name", "normalized_name"),)


class MemoryUnitEntity(Base):
    __tablename__ = "memory_unit_entities"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, default="mention")


class MemoryLink(Base):
    __tablename__ = "memory_links"

    source_memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    link_type: Mapped[str] = mapped_column(Text, primary_key=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_memory_links_source", "source_memory_id"),
        Index("idx_memory_links_target", "target_memory_id"),
    )


class MentalModel(Base):
    __tablename__ = "mental_models"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_directive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trigger: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    source_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )


class MemoryProfile(Base):
    __tablename__ = "memory_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default="default")
    background: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skepticism: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    literalism: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    empathy: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )


class HindsightDocumentState(Base):
    __tablename__ = "hindsight_document_state"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    error_msg: Mapped[str | None] = mapped_column(Text)
    memory_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    link_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        onupdate=_utcnow,
        nullable=False,
    )

    __table_args__ = (Index("idx_hindsight_document_state_status", "status"),)


class HindsightGraphOutbox(Base):
    """Durable PostgreSQL event for rebuilding the disposable Neo4j projection.

    ``document_id`` intentionally has no foreign key: delete events must survive
    deletion of the authoritative Document row.
    """

    __tablename__ = "hindsight_graph_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default="pending",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_msg: Mapped[str | None] = mapped_column(Text)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
        onupdate=_utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('replace', 'delete')",
            name="ck_hindsight_graph_outbox_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_hindsight_graph_outbox_status",
        ),
        Index(
            "idx_hindsight_graph_outbox_ready",
            "status",
            "available_at",
            "id",
        ),
        Index("idx_hindsight_graph_outbox_document", "document_id", "id"),
    )
