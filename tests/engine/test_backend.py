import asyncio
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.settings import InfraSettings
from src.engine.config import EngineConfig
from src.engine.graphrag import backend as backend_mod
from src.engine.interface import DocumentRef, IngestSource
from src.engine.graphrag.backend import (
    GraphRAGBackend,
    _remove_upload_directory,
    _safe_filename,
    build,
)


async def _drain_background_tasks(timeout: float = 5.0) -> None:
    """等待注册表中的后台任务（含失败收尾任务）全部结束。"""

    async def _drain() -> None:
        while backend_mod._background_tasks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_drain(), timeout)


def _failed_write_params(statements: list) -> list[dict]:
    """从 UPDATE 语句中提取 status='failed' 的写入参数。"""
    writes = []
    for stmt in statements:
        try:
            params = stmt.compile().params
        except Exception:
            continue
        if params.get("status") == "failed":
            writes.append(params)
    return writes


class _UpdateRecordingSession:
    """记录 execute 语句的最小 Session 替身（供 _mark_document_failed）。"""

    def __init__(self, statements: list):
        self.statements = statements

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return None

    async def commit(self):
        return None


class _QueryResult:
    def __init__(self, *, scalar_value=0, items=None):
        self._scalar_value = scalar_value
        self._items = items or []

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._items)


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


async def test_document_browse_filters_internal_conversation_sources(monkeypatch):
    visible = SimpleNamespace(
        id=uuid.uuid4(),
        title="visible.md",
        file_type="markdown",
        status="indexed",
        overview="",
        created_at=None,
        updated_at=None,
    )

    class Session:
        def __init__(self):
            self.statements = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _QueryResult(scalar_value=1)
            return _QueryResult(items=[visible])

    session = Session()
    monkeypatch.setattr(backend_mod, "async_session_factory", lambda: session)
    backend = GraphRAGBackend(SimpleNamespace(), SimpleNamespace())

    result = await backend.list_documents()

    statements = [str(statement).lower() for statement in session.statements]
    assert all("documents.file_type not in" in statement for statement in statements)
    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == [str(visible.id)]


async def test_internal_conversation_document_is_not_read_editable_or_removable(
    monkeypatch,
):
    internal = SimpleNamespace(
        id=uuid.uuid4(),
        title="Conversation turn",
        file_type="conversation",
        raw_text="private transcript",
        status="indexed",
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _uid):
            return internal

    pipeline = SimpleNamespace(before_remove=lambda *_args: None)

    async def fake_delete_document_graph(*_args):
        return None

    neo4j = SimpleNamespace(delete_document_graph=fake_delete_document_graph)
    monkeypatch.setattr(backend_mod, "async_session_factory", Session)
    backend = GraphRAGBackend(neo4j, pipeline)

    assert await backend.get_document(str(internal.id)) is None
    with pytest.raises(ValueError, match="文档不存在"):
        await backend.edit_content(str(internal.id), "replacement")
    with pytest.raises(ValueError, match="文档不存在"):
        await backend.reingest(str(internal.id))
    await backend.remove(str(internal.id))


async def test_edit_content_creates_new_version_and_schedules_reindex(monkeypatch):
    """版本化编辑：保存生成新版本（旧行退位），重索引新行。"""
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        title="week.md",
        file_type="markdown",
        status="indexed",
        overview="old overview",
        error_msg="old error",
        raw_text="old text",
        content_hash="old-hash",
        bank_id="default-team",
        tags=[],
        version_group=document_id,
        version_number=1,
        version_of=None,
        is_current=True,
    )

    added: list = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, uid):
            return document if uid == document_id else added[0]

        def add(self, obj):
            added.append(obj)

        async def execute(self, statement):
            # 旧版退位（生产中是 UPDATE ... SET is_current=false）
            document.is_current = False
            return None

        async def commit(self):
            return None

        async def refresh(self, obj):
            return None

    calls = []

    class FakePipeline:
        async def reindex_document(self, uid, content, previous_version=None):
            calls.append((uid, content))

    monkeypatch.setattr(backend_mod, "async_session_factory", FakeSession)
    monkeypatch.setattr(
        backend_mod, "_remove_upload_directory", lambda *_args, **_kwargs: None
    )
    backend = GraphRAGBackend(SimpleNamespace(), FakePipeline())

    result = await backend.edit_content(str(document_id), "updated")
    await asyncio.sleep(0)

    # 新版本行已创建并挂链
    assert len(added) == 1
    new_doc = added[0]
    assert new_doc.bank_id == document.bank_id
    assert new_doc.tags == document.tags
    assert new_doc.version_number == 2
    assert new_doc.version_of == document_id
    assert new_doc.version_group == document_id
    assert document.is_current is False
    assert new_doc.raw_text == "updated"
    # 重索引调度到新版本行
    assert len(calls) == 1
    assert calls[0][0] == new_doc.id
    assert calls[0][1] == "updated"
    assert result.id == str(new_doc.id)


@pytest.mark.parametrize("has_raw_text", [True, False])
async def test_reingest_schedules_the_available_retry_path(
    monkeypatch, tmp_path, has_raw_text
):
    document_id = uuid.uuid4()
    source_path = tmp_path / "week.md"
    source_path.write_text("source content", encoding="utf-8")
    document = SimpleNamespace(
        id=document_id,
        title="week.md",
        file_type="markdown",
        raw_text="indexed content" if has_raw_text else "",
        file_path=str(source_path),
        status="failed",
        overview="",
        error_msg="processing failed",
    )

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, uid):
            return document if uid == document_id else None

        async def execute(self, _statement):
            document.status = "pending"
            document.error_msg = None

        async def commit(self):
            return None

        async def refresh(self, _document):
            return None

    calls = []

    class FakePipeline:
        async def reindex_document(self, uid, content):
            calls.append(("reindex", uid, content))

        async def process_file(self, uid, file_path, title, file_type):
            calls.append(("extract", uid, file_path, title, file_type))

    monkeypatch.setattr(backend_mod, "async_session_factory", FakeSession)
    backend = GraphRAGBackend(SimpleNamespace(), FakePipeline())

    result = await backend.reingest(str(document_id))
    await asyncio.sleep(0)

    assert result.status == "pending"
    assert document.error_msg is None
    if has_raw_text:
        assert calls == [("reindex", document_id, "indexed content")]
    else:
        assert calls == [("extract", document_id, source_path, "week.md", "markdown")]


def test_upload_dir_follows_uploads_dir_setting(monkeypatch, tmp_path):
    # UPLOAD_DIR is settings-driven: an absolute UPLOADS_DIR is honored, and
    # without it the module keeps the relative default. Both modules are
    # reloaded (and restored) because the binding happens at import time.
    import importlib

    import config.settings as settings_mod

    monkeypatch.delenv("UPLOADS_DIR", raising=False)
    assert backend_mod.UPLOAD_DIR == Path(InfraSettings(_env_file=None).uploads_dir)
    assert backend_mod.UPLOAD_DIR == Path("uploads")

    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    try:
        importlib.reload(settings_mod)
        importlib.reload(backend_mod)
        assert backend_mod.UPLOAD_DIR == tmp_path / "uploads"
    finally:
        monkeypatch.delenv("UPLOADS_DIR", raising=False)
        importlib.reload(settings_mod)
        importlib.reload(backend_mod)


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


def test_safe_filename_strips_directory_components():
    # Names carrying a relative path (e.g. a multipart filename) must collapse
    # to a single component so the on-disk write never nests into a missing dir.
    assert _safe_filename("research/coral-resilience-paper.pdf") == (
        "coral-resilience-paper.pdf"
    )
    assert _safe_filename("a/b/c.md") == "c.md"


def test_safe_filename_blocks_path_traversal():
    # A caller-supplied name must never escape the per-doc upload directory.
    assert _safe_filename("../etc/passwd") == "passwd"
    assert _safe_filename("/etc/passwd") == "passwd"
    assert _safe_filename("..") == "document"
    assert _safe_filename("/") == "document"
    assert _safe_filename("") == "document"


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

    assert settings.llm.base_url, "live test requires a configured LLM"
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


async def test_ingest_batch_schedules_pipeline_per_file(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    document_ids = iter([uuid.uuid4() for _ in range(2)])

    class _EmptyResult:
        def scalar_one_or_none(self):
            return None

        def all(self):
            return []

    class FakeSession:
        def __init__(self):
            self.doc = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, doc):
            self.doc = doc

        async def execute(self, _statement):
            return _EmptyResult()

        async def commit(self):
            return None

        async def refresh(self, _doc):
            self.doc.id = next(document_ids)

    calls = []

    class FakePipeline:
        async def process_file(self, doc_id, file_path, title, file_type):
            calls.append((doc_id, file_path, title, file_type))

    sessions = iter([FakeSession(), FakeSession()])
    monkeypatch.setattr(backend_mod, "async_session_factory", lambda: next(sessions))
    backend = GraphRAGBackend(SimpleNamespace(), FakePipeline())

    refs = await backend.ingest_batch(
        [
            IngestSource(name="a.md", data=b"# A"),
            IngestSource(name="b.md", data=b"# B"),
        ]
    )
    await asyncio.sleep(0)

    assert [r.title for r in refs] == ["a.md", "b.md"]
    assert all(r.status == "pending" for r in refs)
    assert len(calls) == 2
    assert (tmp_path / "uploads").is_dir()


async def test_ingest_batch_isolates_per_file_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    backend = GraphRAGBackend(SimpleNamespace(), SimpleNamespace())
    ok_ref = DocumentRef(
        id=str(uuid.uuid4()), title="ok.md", file_type="markdown", status="pending"
    )

    async def fake_ingest_one(source):
        from src.engine.interface import IngestSource  # noqa: F401

        if source.name == "bad.md":
            raise RuntimeError("disk full")
        return ok_ref, None

    monkeypatch.setattr(backend, "_ingest_one", fake_ingest_one)

    refs = await backend.ingest_batch(
        [
            IngestSource(name="bad.md", data=b"x"),
            IngestSource(name="ok.md", data=b"y"),
        ]
    )

    assert refs[0].status == "failed"
    assert "disk full" in refs[0].error_msg
    assert refs[1] is ok_ref


# ── 后台任务注册表（PR #4 P2 + PR #6 N2，design D4）───────────────────


async def test_get_neighbors_threads_hops_and_maps_links():
    from src.engine.components.store.neo4j import GraphQueryResult

    captured = {}

    class FakeNeo4j:
        async def query_neighbors(self, name, hops=2):
            captured["args"] = (name, hops)
            return (
                [
                    GraphQueryResult(
                        name="Acme",
                        entity_type="Company",
                        properties={"description": "园区公司"},
                    )
                ],
                [
                    {
                        "source": "Acme",
                        "target": "Bob",
                        "type": "EMPLOYS",
                        "description": "works",
                    }
                ],
            )

    backend = GraphRAGBackend(FakeNeo4j(), SimpleNamespace())

    data = await backend.get_neighbors("Acme", hops=1)

    assert captured["args"] == ("Acme", 1)  # hops 透传到 neo4j 查询
    assert [(n.name, n.type) for n in data.nodes] == [("Acme", "Company")]
    assert [(link.source, link.target, link.type) for link in data.links] == [
        ("Acme", "Bob", "EMPLOYS")
    ]


async def test_public_vector_search_excludes_conversation_documents(monkeypatch):
    from src.engine.graphrag import _search as search_mod

    class Session:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return SimpleNamespace(all=lambda: [])

    class FakeEmbedder:
        async def embed_text(self, _text):
            return [0.0]

    monkeypatch.setattr(search_mod, "embedder", FakeEmbedder())
    session = Session()

    await search_mod.vector_search(session, "query")

    sql = str(session.statements[0]).lower()
    assert "documents.file_type not in" in sql  # 会话转录 chunk 不进入公共检索
    assert "documents.is_current" in sql  # PR #6 的当前版本过滤保持


async def test_background_task_failure_marks_document_failed_and_logs(
    monkeypatch, caplog
):
    statements: list = []
    monkeypatch.setattr(
        backend_mod,
        "async_session_factory",
        lambda: _UpdateRecordingSession(statements),
    )
    document_id = uuid.uuid4()

    async def dying():
        raise RuntimeError("reindex died before pipeline status handling")

    task = backend_mod._schedule_background(
        dying(), document_id=document_id, label="测试任务"
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            await task
        await _drain_background_tasks()

    writes = _failed_write_params(statements)
    assert len(writes) == 1
    assert writes[0]["error_msg"] == (
        "RuntimeError: reindex died before pipeline status handling"
    )
    assert document_id in writes[0].values()  # 标记的是受影响的文档行
    assert "异常终止" in caplog.text
    assert not backend_mod._background_tasks


async def test_background_task_registry_discards_completed_task(monkeypatch):
    statements: list = []
    monkeypatch.setattr(
        backend_mod,
        "async_session_factory",
        lambda: _UpdateRecordingSession(statements),
    )

    async def finishing():
        return "done"

    task = backend_mod._schedule_background(
        finishing(), document_id=uuid.uuid4(), label="完成任务"
    )

    assert await task == "done"
    await _drain_background_tasks()
    assert not backend_mod._background_tasks
    assert _failed_write_params(statements) == []


async def test_cancelled_background_task_is_not_reported_as_failure(
    monkeypatch, caplog
):
    statements: list = []
    monkeypatch.setattr(
        backend_mod,
        "async_session_factory",
        lambda: _UpdateRecordingSession(statements),
    )

    started = asyncio.Event()

    async def hanging():
        await started.wait()

    task = backend_mod._schedule_background(
        hanging(), document_id=uuid.uuid4(), label="取消任务"
    )
    await asyncio.sleep(0)  # 任务已启动
    task.cancel()
    with caplog.at_level(logging.INFO):
        with pytest.raises(asyncio.CancelledError):
            await task
        await _drain_background_tasks()

    assert _failed_write_params(statements) == []  # 取消 ≠ 失败
    assert "已取消" in caplog.text
    assert not backend_mod._background_tasks


async def test_versioned_edit_reindex_failure_marks_new_version_failed_and_reingest_recovers(
    monkeypatch, tmp_path
):
    """PR #6 N2：编辑后重索引死亡 → 新版本行 failed + 非空错误；重新处理可恢复。"""
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        title="week.md",
        file_type="markdown",
        status="indexed",
        overview="",
        error_msg=None,
        raw_text="old text",
        content_hash="old-hash",
        bank_id="default-team",
        tags=[],
        version_group=document_id,
        version_number=1,
        version_of=None,
        is_current=True,
    )
    documents: dict = {document_id: document}
    failed_writes: list[dict] = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, uid):
            if uid == document_id:
                return document
            # add() 后未 commit 的 SimpleNamespace 没有 id 属性变化，直接查字典
            return next(
                (d for d in documents.values() if getattr(d, "id", None) == uid), None
            )

        def add(self, obj):
            documents[obj.id] = obj

        async def execute(self, statement):
            try:
                params = statement.compile().params
            except Exception:
                return None
            status = params.get("status")
            if status == "failed":
                failed_writes.append(params)
            if status is not None:
                for value in params.values():
                    if isinstance(value, uuid.UUID) and value in documents:
                        documents[value].status = status
                        documents[value].error_msg = params.get("error_msg")
            return None

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

        async def delete(self, _obj):
            return None

    class FlakyPipeline:
        def __init__(self):
            self.fail = True
            self.calls = []

        async def reindex_document(self, uid, content, previous_version=None):
            self.calls.append((uid, content))
            if self.fail:
                raise RuntimeError("reindex died")

    pipeline = FlakyPipeline()
    monkeypatch.setattr(backend_mod, "async_session_factory", Session)
    backend = GraphRAGBackend(SimpleNamespace(), pipeline)

    result = await backend.edit_content(str(document_id), "updated text")
    await _drain_background_tasks()

    # 新版本行被标记 failed，且带非空错误
    new_id = uuid.UUID(result.id)
    assert new_id != document_id
    assert result.version_number == 2
    assert len(failed_writes) == 1
    assert failed_writes[0]["error_msg"] == "RuntimeError: reindex died"
    assert new_id in failed_writes[0].values()
    assert documents[new_id].status == "failed"

    # 重新处理恢复：pipeline 修好后，retry 把新版本行重新索引
    pipeline.fail = False
    recovered = await backend.reingest(result.id)
    await _drain_background_tasks()

    assert recovered.status == "pending"
    assert documents[new_id].status == "pending"
    assert documents[new_id].error_msg is None
    assert pipeline.calls[-1] == (new_id, "updated text")


async def test_remove_deletes_the_document_upload_directory(monkeypatch, tmp_path):
    """QA follow-up #2 回归：remove() 必须删除 uploads/<id>/ 目录。"""
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        title="t.md",
        file_type="markdown",
        status="indexed",
    )
    upload_dir = tmp_path / "uploads"
    doc_dir = upload_dir / str(document_id)
    doc_dir.mkdir(parents=True)
    (doc_dir / "t.md").write_text("content", encoding="utf-8")

    real_remove = backend_mod._remove_upload_directory

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, uid):
            return document

        async def execute(self, _statement):
            return None

        async def commit(self):
            return None

        async def delete(self, _obj):
            return None

    async def fake_delete_document_graph(*_args):
        return None

    # 包装而非替换：调用被记到 tmp 目录的真实删除上，remove() 漏掉
    # 清理调用时该测试必须失败。
    calls: list[uuid.UUID] = []

    def wrapped_remove(uid: uuid.UUID) -> None:
        calls.append(uid)
        real_remove(uid, upload_dir)

    async def fake_before_remove(*_args):
        return None

    monkeypatch.setattr(backend_mod, "async_session_factory", Session)
    monkeypatch.setattr(backend_mod, "_remove_upload_directory", wrapped_remove)
    backend = GraphRAGBackend(
        SimpleNamespace(delete_document_graph=fake_delete_document_graph),
        SimpleNamespace(before_remove=fake_before_remove),
    )

    await backend.remove(str(document_id))

    assert calls == [document_id]
    assert not doc_dir.exists()
    assert upload_dir.exists()


@pytest.mark.parametrize(
    "operation,args",
    [
        ("edit_document", ("changed",)),
        ("propose_edit", ("change title",)),
        ("list_versions", ()),
        ("diff_versions", (1, 2)),
    ],
)
async def test_version_operations_reject_foreign_bank(monkeypatch, operation, args):
    from src.engine.scope import MemoryScope
    from src.engine.components.store.models import Document

    document_id = uuid.uuid4()
    document = Document(
        id=document_id, bank_id="foreign", tags=[], title="private", file_type="pdf"
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_):
            return document

    monkeypatch.setattr(backend_mod, "async_session_factory", Session)
    backend = GraphRAGBackend(
        SimpleNamespace(), SimpleNamespace(), scope=MemoryScope(bank_id="local")
    )
    with pytest.raises(ValueError):
        await getattr(backend, operation)(str(document_id), *args)
