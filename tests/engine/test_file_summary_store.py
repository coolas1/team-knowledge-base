from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.engine.components.store.file_summary import FileSummaryStore, SummaryIdentity
from src.engine.components.store.file_summary_migration import migrate_file_summaries
from src.engine.scope import MemoryScope, TagFilter


def test_summary_identity_invalidates_for_every_generation_input():
    identity = SummaryIdentity.for_text("全文", "标题", "model-a")
    assert identity == SummaryIdentity.for_text("全文", "标题", "model-a")
    variants = [
        SummaryIdentity.for_text("新全文", "标题", "model-a"),
        SummaryIdentity.for_text("全文", "新标题", "model-a"),
        replace(identity, model="model-b"),
        replace(identity, policy_version="v2"),
        replace(identity, template_version="v2"),
        replace(identity, input_chars=100),
        replace(identity, output_chars=100),
    ]
    assert all(item.key != identity.key for item in variants)
    with pytest.raises(ValueError):
        replace(identity, input_chars=0)


def store_with_session():
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.begin = MagicMock(return_value=AsyncMock())
    return FileSummaryStore(
        lambda: session,
        scope=MemoryScope(
            bank_id="team-a", visibility=TagFilter(("private",), "exact")
        ),
    ), session


async def test_summary_lookup_requires_current_owner_and_success():
    store, session = store_with_session()
    session.scalar.return_value = None
    identity = SummaryIdentity.for_text("text", "title", "model")
    assert await store.get(str(uuid4()), identity) is None
    sql = str(session.scalar.call_args.args[0].compile(dialect=postgresql.dialect()))
    params = session.scalar.call_args.args[0].compile().params
    assert "JOIN documents" in sql
    assert "documents.bank_id" in sql and "documents.tags" in sql
    assert "success" in params.values()
    assert identity.key in params.values()


async def test_summary_save_checks_scope_and_preserves_success():
    store, session = store_with_session()
    identity = SummaryIdentity.for_text("text", "title", "model")
    session.scalar.return_value = None
    with pytest.raises(ValueError, match="not visible"):
        await store.save(
            str(uuid4()), identity, summary="summary", coverage={"complete": True}
        )
    session.execute.assert_not_awaited()
    session.scalar.return_value = uuid4()
    await store.save(
        str(session.scalar.return_value),
        identity,
        summary="summary",
        coverage={"complete": True},
    )
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql and "WHERE file_summaries.status !=" in sql
    with pytest.raises(ValueError, match="empty"):
        await store.save(str(uuid4()), identity, summary=" ", coverage={})


async def test_migration_rejects_unsafe_schema_before_connecting():
    engine = MagicMock()
    with pytest.raises(ValueError):
        await migrate_file_summaries(engine, schema="public; drop schema public")
    engine.begin.assert_not_called()


def test_rebuild_fixture_provenance_and_long_tail_are_replayable():
    fixture = json.loads(
        Path("tests/fixtures/file_memory_rebuild.json").read_text(encoding="utf-8")
    )
    documents = {d["id"]: d for d in fixture["documents"]}
    facts = {f["id"]: f for f in fixture["facts"]}
    assert all(f["document_id"] in documents for f in facts.values())
    affected = [
        o["id"]
        for o in fixture["observations"]
        if any(
            facts[f]["document_id"] in fixture["target_documents"]
            for f in o["fact_ids"]
        )
    ]
    assert affected == fixture["expected_affected_observations"]
    assert all(
        facts[f]["document_id"] not in fixture["target_documents"]
        for f in fixture["expected_preserved_facts"]
    )
    long = documents["long-file"]
    text = long["prefix"] + long["repeat_text"] * long["repeat_count"] + long["suffix"]
    assert len(text) > 24000 and text.endswith(long["suffix"])
