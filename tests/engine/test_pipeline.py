"""Pipeline orchestration test.

Full execution needs Postgres + Neo4j + Ollama + a configured LLM, so it is
marked integration. The non-integration assertion verifies the class is wired
against the new component paths (importable, correct constructor signature).
"""

import inspect

import pytest

from src.engine.components.analyzer import Analyzer
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.graphrag.pipeline import Pipeline


def test_pipeline_constructor_accepts_analyzer():
    sig = inspect.signature(Pipeline.__init__)
    assert "analyzer" in sig.parameters
    assert "index_hook" in sig.parameters


def test_pipeline_methods_exist():
    assert hasattr(Pipeline, "process_file")
    assert hasattr(Pipeline, "reindex_document")


@pytest.mark.asyncio
async def test_secondary_index_hook_failure_is_isolated():
    class FailingHook:
        async def after_indexed(self, **kwargs):
            raise RuntimeError("secondary unavailable")

        async def before_remove(self, document_id: str):
            raise RuntimeError("secondary unavailable")

    pipe = Pipeline(Neo4jClient(), index_hook=FailingHook())

    await pipe._notify_indexed(
        document_id="document-1",
        title="week.md",
        content="content",
        file_type="markdown",
    )
    await pipe.before_remove("document-1")


@pytest.mark.integration
async def test_process_file_end_to_end():
    # Requires: docker compose up postgres; Neo4j running; Ollama with
    # nomic-embed-text; an LLM configured via .env (LLM_PROVIDER etc.).
    from uuid import uuid4

    from src.engine.components.store.postgres import init_db, async_session_factory
    from src.engine.components.store.models import Document

    await init_db()
    neo4j = Neo4jClient()
    pipe = Pipeline(neo4j, analyzer=Analyzer())
    doc_id = uuid4()
    async with async_session_factory() as session:
        session.add(
            Document(id=doc_id, title="t.md", file_type="markdown", status="pending")
        )
        await session.commit()
    await pipe.reindex_document(doc_id, "# T\n\nSome content about Acme.")
    await neo4j.close()
