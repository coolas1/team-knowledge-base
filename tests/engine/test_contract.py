"""Shared KnowledgeBase contract: every implementation must satisfy this.

Runs against FakeKnowledgeBase always; against GraphRAGBackend only when
RUN_INTEGRATION=1 (needs Postgres + Neo4j + Ollama).
"""
import asyncio
import os

import pytest

from src.engine.interface import IngestSource, RecallRequest
from tests.conftest import FakeKnowledgeBase


def _make_fake() -> FakeKnowledgeBase:
    return FakeKnowledgeBase()


def _make_graphrag():
    from pathlib import Path
    from src.engine.config import EngineConfig, build_engine

    backend = build_engine(
        EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    )
    _LIVE_BACKENDS.append(backend)
    return backend


_LIVE_BACKENDS = []


@pytest.fixture(autouse=True)
async def _close_live_backend_connections():
    yield
    if os.environ.get("RUN_INTEGRATION") != "1":
        return
    from src.engine.graphrag.backend import _background_tasks

    pending = list(_background_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    while _LIVE_BACKENDS:
        backend = _LIVE_BACKENDS.pop()
        await backend._neo4j.close()
    from src.engine.components.store.postgres import engine

    await engine.dispose()


BACKENDS = [("fake", _make_fake)]
if os.environ.get("RUN_INTEGRATION") == "1":
    BACKENDS.append(("graphrag", _make_graphrag))


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_ingest_returns_doc_ref(name, factory):
    kb = factory()
    ref = await kb.ingest(IngestSource(name="t.md", data=b"# T\n\nbody"))
    try:
        assert ref.id
        assert ref.title == "t.md"
        assert ref.status
    finally:
        if name == "graphrag" and ref.id:
            from src.engine.graphrag.backend import _background_tasks

            await asyncio.gather(*list(_background_tasks), return_exceptions=True)
            await kb.remove(ref.id)


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_recall_returns_result(name, factory):
    kb = factory()
    res = await kb.recall(RecallRequest(query="anything"))
    assert hasattr(res, "chunks")
    assert hasattr(res, "related_entities")


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_get_graph_returns_graph_data(name, factory):
    from src.engine.interface import GraphData
    kb = factory()
    g = await kb.get_graph(None)
    assert isinstance(g, GraphData)


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_list_documents_shape(name, factory):
    kb = factory()
    out = await kb.list_documents()
    assert {"total", "page", "page_size", "items"} <= set(out)


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_remove_is_idempotent(name, factory):
    kb = factory()
    # removing a non-existent id must not raise
    await kb.remove("00000000-0000-0000-0000-000000000000")


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_ingest_batch_returns_ref_per_source(name, factory):
    kb = factory()
    refs = await kb.ingest_batch(
        [
            IngestSource(name="a.md", data=b"# A"),
            IngestSource(name="b.md", data=b"# B"),
        ]
    )
    try:
        assert len(refs) == 2
        assert {r.title for r in refs} == {"a.md", "b.md"}
        assert all(r.id for r in refs)
    finally:
        if name == "graphrag":
            from src.engine.graphrag.backend import _background_tasks

            await asyncio.gather(*list(_background_tasks), return_exceptions=True)
            for ref in refs:
                if ref.id:
                    await kb.remove(ref.id)
