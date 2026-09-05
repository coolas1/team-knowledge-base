from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from src.engine.components.store.models import Base, Document, EMBEDDING_DIM
from src.engine.hindsight_components.models import (
    ConversationMemorySource,
    HindsightDocumentState,
    HindsightGraphOutbox,
    MemoryEntity,
    MemoryLink,
    MemoryProfile,
    MemoryUnit,
    MemoryUnitEntity,
    MentalModel,
)


def test_hindsight_tables_share_existing_metadata_and_document_fk() -> None:
    expected = {
        "memory_units",
        "memory_entities",
        "memory_unit_entities",
        "memory_links",
        "mental_models",
        "memory_profiles",
        "hindsight_document_state",
        "hindsight_graph_outbox",
        "conversation_memory_sources",
    }

    assert expected <= set(Base.metadata.tables)
    assert Base.metadata.tables["documents"] is Document.__table__
    foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in MemoryUnit.__table__.c.document_id.foreign_keys
    }
    assert foreign_keys == {"documents.id"}
    state_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in HindsightDocumentState.__table__.c.document_id.foreign_keys
    }
    assert state_foreign_keys == {"documents.id"}
    assert not HindsightGraphOutbox.__table__.c.document_id.foreign_keys
    conversation_foreign_keys = {
        foreign_key.target_fullname
        for foreign_key in ConversationMemorySource.__table__.c.document_id.foreign_keys
    }
    assert conversation_foreign_keys == {"documents.id"}


def test_memory_schema_compiles_for_postgresql_with_expected_vector_dimension() -> None:
    ddl = str(
        CreateTable(MemoryUnit.__table__).compile(dialect=postgresql.dialect())
    ).lower()

    assert f"vector({EMBEDDING_DIM})" in ddl
    assert "on delete cascade" in ddl
    assert MemoryUnit.__table__.c.embedding.type.dim == EMBEDDING_DIM
    assert "lexical_tokens" in MemoryUnit.__table__.c
    lexical_index = next(
        index
        for index in MemoryUnit.__table__.indexes
        if index.name == "idx_memory_units_lexical_tokens"
    )
    assert (
        "using gin"
        in str(CreateIndex(lexical_index).compile(dialect=postgresql.dialect())).lower()
    )


def test_all_hindsight_model_tables_are_distinct() -> None:
    models = [
        MemoryUnit,
        MemoryEntity,
        MemoryUnitEntity,
        MemoryLink,
        MentalModel,
        MemoryProfile,
        HindsightDocumentState,
        HindsightGraphOutbox,
        ConversationMemorySource,
    ]

    assert len({model.__tablename__ for model in models}) == len(models)


def test_graph_outbox_schema_keeps_delete_events_after_document_deletion() -> None:
    ddl = str(
        CreateTable(HindsightGraphOutbox.__table__).compile(
            dialect=postgresql.dialect()
        )
    ).lower()

    assert "hindsight_graph_outbox" in ddl
    assert "foreign key" not in ddl
    assert "replace" in ddl and "delete" in ddl
    assert "pending" in ddl and "processing" in ddl


def test_conversation_memory_source_schema_has_queue_constraints() -> None:
    ddl = str(
        CreateTable(ConversationMemorySource.__table__).compile(
            dialect=postgresql.dialect()
        )
    ).lower()

    assert "foreign key(document_id) references documents" in ddl
    assert "on delete cascade" in ddl
    assert "unique (session_id, turn_id)" in ddl
    for status in ("pending", "processing", "completed", "failed", "cancelled"):
        assert status in ddl
    assert {index.name for index in ConversationMemorySource.__table__.indexes} == {
        "idx_conversation_memory_ready",
        "idx_conversation_memory_session",
    }
