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
from src.engine.components.store.ownership import BankOwned


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryUnit(BankOwned, Base):
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
    lexical_tokens: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True, default=None
    )
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
    scope_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    memory_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            "memory_index",
            name="uq_memory_source_index",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_memory_units_document", "document_id"),
        Index("idx_memory_units_type", "memory_type"),
        Index("idx_memory_units_state", "state"),
        Index(
            "idx_memory_units_lexical_tokens",
            "lexical_tokens",
            postgresql_using="gin",
        ),
        Index("idx_memory_units_occurred_start", "occurred_start"),
        Index("idx_memory_units_occurred_end", "occurred_end"),
    )


class MemoryEntity(BankOwned, Base):
    __tablename__ = "memory_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    identity_key: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False, default="Entity")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_memory_entities_name", "normalized_name"),
        UniqueConstraint(
            "bank_id",
            "normalized_name",
            "identity_key",
            name="uq_memory_entities_bank_identity",
        ),
    )


class MemoryUnitEntity(BankOwned, Base):
    __tablename__ = "memory_unit_entities"
    original_name: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'::text[]"),
    )

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


class MemoryEntityCorrection(BankOwned, Base):
    __tablename__ = "memory_entity_corrections"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    target_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )


class MemoryLink(BankOwned, Base):
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


class MentalModel(BankOwned, Base):
    __tablename__ = "mental_models"
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    bank_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default="default-team", server_default="default-team"
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    refresh_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="full", server_default="full"
    )
    refresh_after_consolidation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    refresh_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness: Mapped[str] = mapped_column(
        Text, nullable=False, default="empty", server_default="empty"
    )
    error_msg: Mapped[str | None] = mapped_column(Text)
    evidence_watermark: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    source_versions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
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


class MentalModelVersion(BankOwned, Base):
    __tablename__ = "mental_model_versions"

    bank_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default="default-team", server_default="default-team"
    )
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list, server_default="{}"
    )
    source_versions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    refresh_mode: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cost_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )


class MentalModelRefreshJob(BankOwned, Base):
    __tablename__ = "mental_model_refresh_jobs"

    bank_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default="default-team", server_default="default-team"
    )
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    requested_watermark: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_msg: Mapped[str | None] = mapped_column(Text)
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


class MemoryProfile(BankOwned, Base):
    __tablename__ = "memory_profiles"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default="default")
    bank_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default="default-team", server_default="default-team"
    )
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


class RetentionRequest(BankOwned, Base):
    __tablename__ = "retention_requests"
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    request_id: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=sql_text("now()"),
        nullable=False,
    )


class ObservationRecord(BankOwned, Base):
    """Mutable head for a derived observation stored in ``memory_units``."""

    __tablename__ = "observation_records"
    memory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    write_scope: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default="{}"
    )
    freshness: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active"
    )
    stale_reason: Mapped[str | None] = mapped_column(Text)
    has_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    processed_through: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
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
        CheckConstraint("version > 0", name="ck_observation_version_positive"),
        CheckConstraint(
            "freshness IN ('active', 'stale', 'tombstoned')",
            name="ck_observation_freshness",
        ),
        Index("idx_observation_scope", "bank_id", "write_scope", "freshness"),
        Index("idx_observation_exact", "bank_id", "normalized_text"),
    )


class ObservationHistory(BankOwned, Base):
    __tablename__ = "observation_history"
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    freshness: Mapped[str] = mapped_column(Text, nullable=False)
    change_kind: Mapped[str] = mapped_column(Text, nullable=False, default="synthesis")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_snapshot: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )


class ObservationEvidence(BankOwned, Base):
    __tablename__ = "observation_evidence"
    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memory_units.id", ondelete="CASCADE"),
        primary_key=True,
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )

    __table_args__ = (Index("idx_observation_evidence_fact", "fact_id", "active"),)


class FactTombstone(BankOwned, Base):
    __tablename__ = "memory_fact_tombstones"
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    fact_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )


class ConsolidationFactEvent(BankOwned, Base):
    __tablename__ = "consolidation_fact_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    scope_key: Mapped[str] = mapped_column(Text, nullable=False)
    write_scope: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[str] = mapped_column(
        Text, nullable=False, default="upsert", server_default="upsert"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=sql_text("now()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "bank_id",
            "scope_key",
            "fact_id",
            "fact_version",
            "operation",
            name="uq_consolidation_fact_version",
        ),
        CheckConstraint(
            "operation IN ('upsert', 'delete')", name="ck_consolidation_event_operation"
        ),
        Index("idx_consolidation_event_scope", "bank_id", "scope_key", "id"),
    )


class ConsolidationJob(BankOwned, Base):
    __tablename__ = "consolidation_jobs"
    scope_key: Mapped[str] = mapped_column(Text, primary_key=True)
    bank_id: Mapped[str] = mapped_column(
        Text, primary_key=True, default="default-team", server_default="default-team"
    )
    write_scope: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    pending_through: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    processed_through: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    iterations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    cost_microusd: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    error_msg: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
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
            "status IN ('pending', 'processing', 'completed', 'failed', 'budget_exhausted')",
            name="ck_consolidation_job_status",
        ),
        Index("idx_consolidation_job_ready", "status", "available_at", "bank_id"),
    )


class HindsightDocumentState(BankOwned, Base):
    __tablename__ = "hindsight_document_state"
    content_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    extraction_cache: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    source_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    stage_results: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )

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


class ConversationMemorySource(BankOwned, Base):
    """Internal conversation document plus its durable retention queue state."""

    __tablename__ = "conversation_memory_sources"
    source_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=sql_text("gen_random_uuid()"),
    )
    stage_results: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
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
        UniqueConstraint(
            "bank_id", "session_id", "turn_id", name="uq_conversation_memory_bank_turn"
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')",
            name="ck_conversation_memory_source_status",
        ),
        Index(
            "idx_conversation_memory_ready",
            "status",
            "available_at",
            "document_id",
        ),
        Index("idx_conversation_memory_session", "session_id", "document_id"),
    )


class HindsightGraphOutbox(BankOwned, Base):
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
