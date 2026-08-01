import inspect

import pytest

from src.engine.config import EngineConfig
from src.engine.graphrag.backend import GraphRAGBackend, build


def test_build_returns_graphrag_backend():
    # build() instantiates Neo4j+Pipeline; it only constructs objects, no I/O
    # until a method is awaited. Verify type without touching services.
    from pathlib import Path

    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    assert isinstance(kb, GraphRAGBackend)


def test_capabilities_declares_graph_and_partial_update():
    from pathlib import Path

    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    assert kb.capabilities.graph is True
    assert kb.capabilities.partial_update is True


def test_build_injects_optional_index_hook():
    from pathlib import Path

    hook = object()
    cfg = EngineConfig(
        impl="graphrag",
        config_dir=Path("config/engine/graphrag"),
        index_hook=hook,  # type: ignore[arg-type]
    )
    kb = build(cfg)

    assert kb._pipeline._index_hook is hook


def test_backend_implements_protocol_methods():
    for name in [
        "ingest",
        "reingest",
        "remove",
        "recall",
        "get_graph",
        "get_neighbors",
        "list_documents",
        "get_document",
    ]:
        assert hasattr(GraphRAGBackend, name), f"missing {name}"


@pytest.mark.integration
async def test_ingest_recall_roundtrip():
    # Requires Postgres + Neo4j + Ollama + LLM configured.
    from pathlib import Path
    from src.engine.components.store.postgres import init_db

    await init_db()
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    ref = await kb.ingest(
        __import__("src.engine.interface", fromlist=["IngestSource"]).IngestSource(
            name="t.md", data=b"# T\n\nAcme is in Building A."
        )
    )
    assert ref.status in ("pending", "indexed")
    res = await kb.recall(
        __import__("src.engine.interface", fromlist=["RecallRequest"]).RecallRequest(
            query="Acme"
        )
    )
    assert hasattr(res, "chunks")
