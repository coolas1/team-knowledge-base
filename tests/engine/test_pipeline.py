"""Pipeline orchestration test.

Full execution needs Postgres + Neo4j + Ollama + a configured LLM, so it is
marked integration. The non-integration assertion verifies the class is wired
against the new component paths (importable, correct constructor signature).
"""

import asyncio
import inspect
from uuid import uuid4

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
async def test_process_file_end_to_end(integration_host_config):
    # Requires: docker compose up postgres; Neo4j running; Ollama with
    # nomic-embed-text; an LLM configured via .env (LLM_PROVIDER etc.).
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

    assert settings.llm_provider != "todo", "live test requires a configured LLM"
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
    return "# T\n\n" + "\n\n".join(
        f"段落 {i} " + "内容文字" * 150 for i in range(8)
    )


async def test_analyze_document_runs_chunks_parallel_and_bounded(monkeypatch):
    from src.engine.graphrag import pipeline as pipeline_mod

    analyzer = _RecordingAnalyzer()
    embed = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "embedder", embed)
    pipe = pipeline_mod.Pipeline(object(), analyzer=analyzer, chunk_concurrency=3)

    doc_analysis, chunk_results, embeddings = await pipe._analyze_document(
        _multi_chunk_text(), "t.md", uuid4()
    )

    assert doc_analysis.overview == "ov"
    assert len(chunk_results) > 3
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
