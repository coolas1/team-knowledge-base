"""Shared KnowledgeBase contract: every implementation must satisfy this.

Runs against FakeKnowledgeBase always; against GraphRAGBackend only when
RUN_INTEGRATION=1 (needs Postgres + Neo4j + Ollama).
"""
import os

import pytest

from src.engine.interface import IngestSource, RecallRequest
from tests.conftest import FakeKnowledgeBase


def _make_fake() -> FakeKnowledgeBase:
    return FakeKnowledgeBase()


def _make_graphrag():
    from pathlib import Path
    from src.engine.config import EngineConfig, build_engine
    return build_engine(EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag")))


BACKENDS = [("fake", _make_fake)]
if os.environ.get("RUN_INTEGRATION") == "1":
    BACKENDS.append(("graphrag", _make_graphrag))


@pytest.mark.parametrize("name,factory", BACKENDS)
async def test_ingest_returns_doc_ref(name, factory):
    kb = factory()
    ref = await kb.ingest(IngestSource(name="t.md", data=b"# T\n\nbody"))
    assert ref.id
    assert ref.title == "t.md"
    assert ref.status


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
