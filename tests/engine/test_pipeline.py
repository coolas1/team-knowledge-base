"""Pipeline orchestration test.

Full execution needs Postgres + Neo4j + Ollama + a configured LLM, so it is
marked integration. The non-integration assertion verifies the class is wired
against the new component paths (importable, correct constructor signature).
"""

import asyncio
import inspect
from uuid import uuid4

import pytest

from src.engine.components.analyzer import Analyzer, ChunkAnalysisResult
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
async def test_process_file_end_to_end(integration_host_config):
    # Requires: docker compose up postgres; Neo4j running; Ollama with
    # nomic-embed-text; an LLM configured via .env (LLM_BASE_URL etc.).
    from uuid import uuid4

    from sqlalchemy import func, select

    from config.settings import settings
    from src.engine.components.store.models import Chunk, Document
    from src.engine.components.store.postgres import (
        async_session_factory,
        engine,
        init_db,
    )
    from src.engine.graphrag.backend import GraphRAGBackend

    assert settings.llm.base_url, "live test requires a configured LLM"
    await init_db()
    neo4j = Neo4jClient()
    pipe = Pipeline(neo4j, analyzer=Analyzer())
    backend = GraphRAGBackend(neo4j, pipe)
    doc_id = uuid4()
    title = f"integration-pipeline-{doc_id}.md"
    content = f"# Integration pipeline\n\nUnique pipeline fact {doc_id}."
    try:
        async with async_session_factory() as session:
            session.add(
                Document(
                    id=doc_id,
                    title=title,
                    file_type="markdown",
                    status="pending",
                )
            )
            await session.commit()

        await pipe.reindex_document(doc_id, content)

        async with async_session_factory() as session:
            document = await session.get(Document, doc_id)
            chunk_count = await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.doc_id == doc_id)
            )
        assert document is not None
        assert document.status == "indexed", document.error_msg
        assert document.error_msg is None
        assert document.raw_text == content
        assert chunk_count and chunk_count > 0
    finally:
        try:
            await backend.remove(str(doc_id))
        finally:
            await neo4j.close()
            await engine.dispose()


def test_pipeline_constructor_accepts_concurrency():
    sig = inspect.signature(Pipeline.__init__)
    assert "chunk_concurrency" in sig.parameters
    assert "doc_concurrency" in sig.parameters


class _RecordingAnalyzer:
    """Tracks peak concurrent analyze_chunk calls; delays each call."""

    def __init__(self, delay: float = 0.05, fail_index: int | None = None):
        self._delay = delay
        self._fail_index = fail_index
        self.in_flight = 0
        self.peak = 0
        self.chunk_calls: list[int] = []

    async def analyze_overview(self, text: str, title: str):
        from src.engine.components.analyzer import AnalysisResult

        await asyncio.sleep(self._delay)
        return AnalysisResult(overview="ov", file_relations=[])

    async def analyze_chunk(self, chunk_text: str, doc_title: str, idx: int):
        from src.engine.components.analyzer import ChunkAnalysisResult

        self.chunk_calls.append(idx)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
            if self._fail_index is not None and idx == self._fail_index:
                raise RuntimeError(f"chunk {idx} failed")
            return ChunkAnalysisResult(chunk_index=idx)
        finally:
            self.in_flight -= 1


class _FakeEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed_batch(self, texts):
        self.calls += 1
        await asyncio.sleep(0.02)
        return [[0.0] for _ in texts]


def _multi_chunk_text() -> str:
    # chunk_size=500 tokens ≈ 1000 字符，每段 ~1200 字符确保切成多块
    return "# T\n\n" + "\n\n".join(f"段落 {i} " + "内容文字" * 150 for i in range(8))


async def test_analyze_document_runs_chunks_parallel_and_bounded(monkeypatch):
    from src.engine.graphrag import pipeline as pipeline_mod

    analyzer = _RecordingAnalyzer()
    embed = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "embedder", embed)
    pipe = pipeline_mod.Pipeline(object(), analyzer=analyzer, chunk_concurrency=3)

    doc_analysis, chunks, chunk_results, embeddings = await pipe._analyze_document(
        _multi_chunk_text(), "t.md", uuid4()
    )

    assert doc_analysis.overview == "ov"
    assert len(chunks) == len(chunk_results) > 3
    assert [ca.chunk_index for ca in chunk_results] == list(range(len(chunk_results)))
    assert 1 < analyzer.peak <= 3  # 并行但受信号量限流
    assert embed.calls == 1
    assert len(embeddings) == len(chunk_results)


async def test_analyze_document_failure_propagates(monkeypatch):
    from src.engine.graphrag import pipeline as pipeline_mod

    analyzer = _RecordingAnalyzer(delay=0.05, fail_index=1)
    monkeypatch.setattr(pipeline_mod, "embedder", _FakeEmbedder())
    pipe = pipeline_mod.Pipeline(object(), analyzer=analyzer, chunk_concurrency=4)

    with pytest.raises(Exception) as excinfo:
        await pipe._analyze_document(_multi_chunk_text(), "t.md", uuid4())

    messages = [str(excinfo.value)] + [
        str(e) for e in getattr(excinfo.value, "exceptions", [])
    ]
    assert any("chunk 1 failed" in m for m in messages)


def test_unwrap_exception_group_single():
    from src.engine.graphrag.pipeline import _unwrap_exception_group

    inner = RuntimeError("boom")
    group = ExceptionGroup("eg", [inner])
    assert _unwrap_exception_group(group) is inner
    assert _unwrap_exception_group(inner) is inner


def test_format_error_is_type_first_and_never_empty():
    from src.engine.graphrag.pipeline import format_error

    assert format_error(RuntimeError("boom")) == "RuntimeError: boom"
    # 无消息异常（如取消/超时）也能标识失败原因
    assert format_error(asyncio.CancelledError()) == "CancelledError"
    assert format_error(TimeoutError()) == "TimeoutError"
    assert format_error(asyncio.CancelledError()).strip()


class _FlakyAnalyzer(_RecordingAnalyzer):
    """前 N 次 chunk 调用抛瞬时异常，之后成功。"""

    def __init__(self, transient_failures: int, error: Exception):
        super().__init__(delay=0.01)
        self._remaining = transient_failures
        self._error = error
        self.attempts = 0

    async def analyze_chunk(self, chunk_text, doc_title, idx):
        from src.engine.components.analyzer import ChunkAnalysisResult

        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return ChunkAnalysisResult(chunk_index=idx)


async def test_analyze_document_retries_transient_llm_failures(monkeypatch):
    import httpx

    from src.engine.graphrag import pipeline as pipeline_mod

    analyzer = _FlakyAnalyzer(1, httpx.ConnectError("endpoint saturated"))
    monkeypatch.setattr(pipeline_mod, "embedder", _FakeEmbedder())
    pipe = pipeline_mod.Pipeline(
        object(), analyzer=analyzer, chunk_concurrency=2, llm_retries=2,
        llm_backoff_base_seconds=0.001,
    )

    doc_analysis, _chunks, chunk_results, _embeddings = await pipe._analyze_document(
        "# T\n\n短文本", "t.md", uuid4()
    )

    assert doc_analysis.overview == "ov"
    assert len(chunk_results) >= 1  # 瞬时失败自愈，文档继续分析


async def test_analyze_document_does_not_retry_non_transient(monkeypatch):
    from src.engine.graphrag import pipeline as pipeline_mod

    analyzer = _FlakyAnalyzer(1, ValueError("bad schema"))
    monkeypatch.setattr(pipeline_mod, "embedder", _FakeEmbedder())
    pipe = pipeline_mod.Pipeline(
        object(), analyzer=analyzer, chunk_concurrency=2, llm_retries=3,
        llm_backoff_base_seconds=0.001,
    )

    with pytest.raises(Exception) as excinfo:
        await pipe._analyze_document("# T\n\n短文本", "t.md", uuid4())

    messages = [str(excinfo.value)] + [
        str(e) for e in getattr(excinfo.value, "exceptions", [])
    ]
    assert any("bad schema" in m for m in messages)
    assert analyzer.attempts == 1  # 非瞬时异常不重试


async def test_mark_failed_writes_non_empty_error_for_messageless_exception(
    monkeypatch,
):
    from types import SimpleNamespace

    from src.engine.graphrag import pipeline as pipeline_mod

    doc_id = uuid4()
    doc = SimpleNamespace(id=doc_id, status="processing")
    session = _PipelineSession({doc_id: doc})
    monkeypatch.setattr(pipeline_mod, "async_session_factory", lambda: session)

    pipe = pipeline_mod.Pipeline(object(), analyzer=_RecordingAnalyzer())
    await pipe._mark_failed(doc_id, asyncio.CancelledError(), "Pipeline")

    statuses = _doc_statuses(session)
    assert statuses[-1] == "failed"
    # error_msg 非空且标识异常类型
    for stmt in session.statements:
        try:
            params = stmt.compile().params
        except Exception:
            continue
        if params.get("status") == "failed":
            assert params["error_msg"].strip()
            assert "CancelledError" in params["error_msg"]


class _RecordingNeo4j:
    def __init__(self):
        self.entity_items = None
        self.relation_items = None
        self.doc_nodes = []

    async def upsert_entities_batch(self, items):
        self.entity_items = items

    async def upsert_relations_batch(self, items):
        self.relation_items = items

    async def upsert_document_node(self, **kwargs):
        self.doc_nodes.append(kwargs)

    async def delete_document_graph(self, doc_id):
        self.deleted = doc_id


async def test_write_chunk_graph_flattens_analyses_into_batches():
    from src.engine.components.analyzer import Entity, Relation

    neo = _RecordingNeo4j()
    pipe = Pipeline(neo, analyzer=_RecordingAnalyzer())
    analyses = [
        ChunkAnalysisResult(
            chunk_index=0,
            entities=[Entity(name="Acme", type="Organization")],
            relations=[Relation(from_name="Acme", to_name="Bob", type="EMPLOYS")],
        ),
        ChunkAnalysisResult(
            chunk_index=1,
            entities=[Entity(name="Acme", type="Organization")],
            relations=[],
        ),
    ]

    await pipe._write_chunk_graph("doc-1", "t.md", analyses)

    assert [(e.name, s.chunk_index) for e, s in neo.entity_items] == [
        ("Acme", 0),
        ("Acme", 1),
    ]
    assert [(r.from_name, s.chunk_index) for r, s in neo.relation_items] == [
        ("Acme", 0)
    ]


class _PipelineSession:
    """记录语句的最小 Session 替身（见 test_backend.py 的同类模式）。"""

    def __init__(self, docs):
        self.docs = docs  # doc_id -> doc（get 按 id 查）
        self.statements = []
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, _model, key):
        return self.docs.get(key)

    async def execute(self, statement):
        self.statements.append(statement)
        from types import SimpleNamespace

        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None

    def add(self, obj):
        self.added.append(obj)


def _doc_statuses(session):
    statuses = []
    for stmt in session.statements:
        try:
            params = stmt.compile().params
        except Exception:
            continue
        if "status" in params:
            statuses.append(params["status"])
    return statuses


class _ThreadRecordingRegistry:
    def __init__(self):
        self.thread_ids = []

    def extract(self, file_path):
        import threading

        self.thread_ids.append(threading.get_ident())
        return "# T\n\n" + "内容文字" * 300


@pytest.mark.parametrize("vector_only", [False, True])
async def test_process_file_indexes_document_with_fakes(
    monkeypatch, tmp_path, vector_only
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.engine.graphrag import pipeline as pipeline_mod

    doc_id = uuid4()
    doc = SimpleNamespace(
        id=doc_id, content_hash=None, status="pending", bank_id="default-team", tags=[]
    )
    session = _PipelineSession({doc_id: doc})
    monkeypatch.setattr(pipeline_mod, "async_session_factory", lambda: session)
    registry_stub = _ThreadRecordingRegistry()
    monkeypatch.setattr(pipeline_mod, "registry", registry_stub)
    monkeypatch.setattr(pipeline_mod, "embedder", _FakeEmbedder())
    neo = _RecordingNeo4j()
    file_path = tmp_path / "t.md"
    file_path.write_text("# T", encoding="utf-8")

    analyzer = _RecordingAnalyzer()
    analyzer.summarize_document = analyzer.analyze_overview
    pipe = Pipeline(neo, analyzer=analyzer, vector_only=vector_only)
    pipe._notify_indexed = AsyncMock()
    await pipe.process_file(doc_id, file_path, "t.md", "markdown")

    import threading

    assert registry_stub.thread_ids == [] or all(
        t != threading.get_ident() for t in registry_stub.thread_ids
    )  # 提取在线程池执行，不阻塞事件循环
    statuses = _doc_statuses(session)
    assert "processing" in statuses
    assert statuses[-1] == "indexed"
    assert len(neo.doc_nodes) == 1
    assert neo.entity_items is not None  # 批量图谱写入被调用
    assert session.added  # chunk 行已入队
    retained = pipe._notify_indexed.call_args.kwargs["content"]
    assert retained.startswith("# T")  # retain resolves the persisted summary by identity
    assert bool(analyzer.chunk_calls) is not vector_only
    stored = [stmt.compile().params for stmt in session.statements]
    assert any(str(params.get("raw_text", "")).startswith("# T") for params in stored)


async def test_process_file_doc_semaphore_serializes(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.engine.graphrag import pipeline as pipeline_mod

    gate = asyncio.Event()
    analyzer = _RecordingAnalyzer()
    analyzer_gate = gate

    async def gated_analyze_chunk(text, title, idx):
        analyzer.chunk_calls.append(idx)
        await analyzer_gate.wait()
        from src.engine.components.analyzer import ChunkAnalysisResult

        return ChunkAnalysisResult(chunk_index=idx)

    async def gated_overview(text, title):
        await analyzer_gate.wait()
        from src.engine.components.analyzer import AnalysisResult

        return AnalysisResult(overview="ov")

    analyzer.analyze_chunk = gated_analyze_chunk
    analyzer.analyze_overview = gated_overview

    docs = {
        doc_id: SimpleNamespace(
            id=doc_id,
            content_hash=None,
            status="pending",
            bank_id="default-team",
            tags=[],
        )
        for doc_id in [uuid4(), uuid4()]
    }
    sessions = []

    def make_session():
        s = _PipelineSession(docs)
        sessions.append(s)
        return s

    monkeypatch.setattr(pipeline_mod, "async_session_factory", make_session)
    registry_stub = _ThreadRecordingRegistry()
    monkeypatch.setattr(pipeline_mod, "registry", registry_stub)
    monkeypatch.setattr(pipeline_mod, "embedder", _FakeEmbedder())
    neo = _RecordingNeo4j()
    paths = []
    for i in range(2):
        p = tmp_path / f"t{i}.md"
        p.write_text("# T", encoding="utf-8")
        paths.append(p)

    pipe = Pipeline(neo, analyzer=analyzer, doc_concurrency=1)
    doc_ids = list(docs)
    t1 = asyncio.create_task(
        pipe.process_file(doc_ids[0], paths[0], "t0.md", "markdown")
    )
    await asyncio.sleep(0.1)  # doc0 进入被 gate 卡住的分析
    assert len(analyzer.chunk_calls) >= 1

    t2 = asyncio.create_task(
        pipe.process_file(doc_ids[1], paths[1], "t1.md", "markdown")
    )
    await asyncio.sleep(0.1)
    assert len(registry_stub.thread_ids) == 1  # doc1 的提取被 doc 信号量挡住

    gate.set()
    await asyncio.gather(t1, t2)
    assert len(registry_stub.thread_ids) == 2
    all_statuses = [st for s in sessions for st in _doc_statuses(s)]
    assert all_statuses.count("indexed") == 2  # 两篇文档均完成
    assert "failed" not in all_statuses
