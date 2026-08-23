import asyncio
import uuid
from types import SimpleNamespace

import pytest

from src.engine.config import EngineConfig
from src.engine.graphrag import backend as backend_mod
from src.engine.graphrag.backend import (
    GraphRAGBackend,
    _remove_upload_directory,
    build,
)


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
        "edit_content",
        "reingest",
        "remove",
        "recall",
        "get_graph",
        "get_neighbors",
        "list_documents",
        "get_document",
    ]:
        assert hasattr(GraphRAGBackend, name), f"missing {name}"


async def test_edit_content_persists_text_and_schedules_reindex(monkeypatch):
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        title="week.md",
        file_type="markdown",
        status="indexed",
        overview="old overview",
        error_msg="old error",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, uid):
            return document if uid == document_id else None

        async def execute(self, _statement):
            document.raw_text = "updated"
            document.status = "pending"
            document.error_msg = None

        async def commit(self):
            return None

        async def refresh(self, _document):
            return None

    calls = []

    class FakePipeline:
        async def reindex_document(self, uid, content):
            calls.append((uid, content))

    monkeypatch.setattr(backend_mod, "async_session_factory", FakeSession)
    backend = GraphRAGBackend(SimpleNamespace(), FakePipeline())

    result = await backend.edit_content(str(document_id), "updated")
    await asyncio.sleep(0)

    assert result.status == "pending"
    assert document.raw_text == "updated"
    assert document.error_msg is None
    assert calls == [(document_id, "updated")]


def test_remove_upload_directory_only_deletes_uuid_scope(tmp_path):
    document_id = uuid.uuid4()
    upload_dir = tmp_path / "uploads"
    document_dir = upload_dir / str(document_id)
    document_dir.mkdir(parents=True)
    (document_dir / "t.md").write_text("test", encoding="utf-8")
    sibling = upload_dir / "keep"
    sibling.mkdir()

    _remove_upload_directory(document_id, upload_dir)

    assert not document_dir.exists()
    assert sibling.exists()
    assert upload_dir.exists()


def test_remove_upload_directory_ignores_missing_directory(tmp_path):
    _remove_upload_directory(uuid.uuid4(), tmp_path / "uploads")

    assert tmp_path.exists()


def test_remove_upload_directory_unlinks_symlink_without_following(tmp_path):
    document_id = uuid.uuid4()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "keep.txt").write_text("keep", encoding="utf-8")
    link = upload_dir / str(document_id)
    try:
        link.symlink_to(protected, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    _remove_upload_directory(document_id, upload_dir)

    assert not link.exists()
    assert (protected / "keep.txt").exists()


@pytest.mark.integration
@pytest.mark.filterwarnings(
    "error:Expected a result with a single record, but found multiple.:UserWarning"
)
async def test_ingest_recall_roundtrip(integration_host_config, monkeypatch):
    # Requires Postgres + Neo4j + Ollama + LLM configured.
    from pathlib import Path
    from sqlalchemy import func, select

    from config.settings import settings
    from src.engine.components.store.models import Chunk, Document
    from src.engine.components.store.postgres import (
        async_session_factory,
        engine,
        init_db,
    )
    from src.engine.interface import IngestSource, RecallRequest

    assert settings.llm_provider != "todo", "live test requires a configured LLM"
    await init_db()
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    kb = build(cfg)
    task = None
    ref = None
    real_create_task = asyncio.create_task

    def capture_task(coroutine):
        nonlocal task
        task = real_create_task(coroutine)
        return task

    monkeypatch.setattr(asyncio, "create_task", capture_task)
    token = f"IntegrationBeacon{uuid.uuid4().hex}"
    try:
        ref = await kb.ingest(
            IngestSource(
                name=f"integration-roundtrip-{uuid.uuid4()}.md",
                data=f"# Integration\n\n{token} is located in Building A.".encode(),
            )
        )
        assert ref.status == "pending"
        assert task is not None
        monkeypatch.setattr(asyncio, "create_task", real_create_task)
        await asyncio.wait_for(task, timeout=300)

        async with async_session_factory() as session:
            document = await session.get(Document, uuid.UUID(ref.id))
            chunk_count = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.doc_id == uuid.UUID(ref.id))
            )
        assert document is not None
        assert document.status == "indexed", document.error_msg
        assert document.error_msg is None
        assert chunk_count and chunk_count > 0

        result = await kb.recall(RecallRequest(query=token, top_k=20))
        assert any(chunk.doc_id == ref.id for chunk in result.chunks)
    finally:
        try:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if ref is not None:
                await kb.remove(ref.id)
                assert not (Path("uploads") / ref.id).exists()
        finally:
            await kb._neo4j.close()
            await engine.dispose()
