"""Run against SCOPE_TEST_DSN only; each run owns a fresh isolated schema."""

import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.engine.components.store.file_summary import FileSummaryStore, SummaryIdentity
from src.engine.components.store.file_summary_migration import migrate_file_summaries
from src.engine.components.store.models import Document
from src.engine.scope import MemoryScope

pytestmark = pytest.mark.integration


async def test_file_summary_upgrade_reuse_scope_and_original_preservation(monkeypatch):
    dsn = os.getenv("SCOPE_TEST_DSN")
    if not dsn:
        pytest.skip("SCOPE_TEST_DSN must select a disposable PostgreSQL database")
    schema = "summary_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        dsn, execution_options={"schema_translate_map": {None: schema}}
    )
    document_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(lambda conn: Document.__table__.create(conn))
            await connection.execute(
                Document.__table__.insert().values(
                    id=document_id,
                    bank_id="team-a",
                    title="legacy",
                    file_type="markdown",
                    raw_text="original text",
                    overview="legacy overview",
                    status="indexed",
                )
            )
        await migrate_file_summaries(engine, schema=schema)
        await migrate_file_summaries(engine, schema=schema)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = FileSummaryStore(sessions, scope=MemoryScope(bank_id="team-a"))
        identity = SummaryIdentity.for_text("original text", "legacy", "model")
        assert await store.get(str(document_id), identity) is None
        await store.save(
            str(document_id), identity, summary="summary", coverage={"complete": True}
        )
        await store.save(
            str(document_id), identity, summary="", coverage={}, error_code="timeout"
        )
        assert (await store.get(str(document_id), identity)).summary == "summary"
        assert (
            await store.with_scope(MemoryScope(bank_id="team-b")).get(
                str(document_id), identity
            )
            is None
        )
        changed = SummaryIdentity.for_text("original text", "legacy", "model-v2")
        assert await store.get(str(document_id), changed) is None
        async with sessions() as session:
            original = await session.scalar(
                select(Document).where(Document.id == document_id)
            )
            assert (
                original.raw_text == "original text"
                and original.overview == "legacy overview"
            )
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from config.settings import settings
        from src.engine.components.analyzer import AnalysisResult
        from src.engine.components.file_summary import FileSummaryManager

        monkeypatch.setattr(
            settings,
            "llm",
            SimpleNamespace(enabled=True, require_model=lambda: "summary-model"),
        )
        analyzer = SimpleNamespace(
            summarize_document=AsyncMock(
                return_value=AnalysisResult(overview="persisted summary")
            )
        )
        manager = FileSummaryManager(
            sessions, analyzer=analyzer, scope=MemoryScope(bank_id="team-a")
        )
        first = await manager.prepare(str(document_id), "original text", "legacy")
        second = await manager.prepare(str(document_id), "original text", "legacy")
        assert first == second
        analyzer.summarize_document.assert_awaited_once()
        await manager.prepare(str(document_id), "new original text", "legacy")
        assert analyzer.summarize_document.await_count == 2
        analyzer.summarize_document.side_effect = [
            RuntimeError("failed"),
            AnalysisResult(overview="recovered"),
        ]
        with pytest.raises(RuntimeError):
            await manager.prepare(str(document_id), "another text", "legacy")
        recovered = await manager.prepare(str(document_id), "another text", "legacy")
        assert recovered.text == "recovered"
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
