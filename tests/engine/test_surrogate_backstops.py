"""registry 之外的 encode 兜底点。

registry 今天已是唯一收敛点，但兜底不能依赖这一点长期成立。这里把代理项
文本绕过 registry 直接送进每个哈希现场，确认没有一个抛 UnicodeEncodeError。
"""

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

from src.engine.graphrag import backend as backend_mod
from src.engine.graphrag import pipeline as pipeline_mod
from src.engine.graphrag._version_match import find_version_candidate
from src.engine.interface import IngestSource

SURROGATE_TEXT = "alpha\udbc3beta"
SURROGATE_DOC_ID = uuid.uuid4()


class _NextStageReached(Exception):
    """哨兵：代码穿过了哈希点并走到下一阶段。"""


class _EmptyResult:
    def scalar_one_or_none(self):
        return None

    def all(self):
        return []


class _StubSession:
    """够用的 session 替身：记录 add()，其余查询返回空。"""

    def __init__(self, doc=None):
        self.doc = doc
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, model, _uid, **_kwargs):
        return self.doc if model is pipeline_mod.Document else None

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, _statement):
        return _EmptyResult()

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


class _SurrogateRegistry:
    """绕过 registry 的归一化，直接吐出带代理项的文本。"""

    def extract(self, _file_path):
        return SURROGATE_TEXT


class _NoopPipeline:
    async def process_file(self, *_args, **_kwargs):
        return None

    async def reindex_document(self, *_args, **_kwargs):
        return None


def test_version_match_hashes_surrogate_text():
    """新旧两侧文本都带代理项时，精确比对照常成立。"""
    candidate = find_version_candidate(
        "规范.md",
        SURROGATE_TEXT,
        [(str(SURROGATE_DOC_ID), "规范.md", SURROGATE_TEXT)],
    )

    assert candidate is not None
    assert candidate.exact_content is True


async def test_process_file_hash_survives_surrogates(monkeypatch, tmp_path):
    """哈希点若被代理项拦下，分析阶段根本不会被执行到。"""
    doc = SimpleNamespace(
        id=SURROGATE_DOC_ID,
        content_hash=None,
        status="pending",
        bank_id="default-team",
        tags=[],
    )
    monkeypatch.setattr(
        pipeline_mod, "async_session_factory", lambda: _StubSession(doc)
    )
    monkeypatch.setattr(pipeline_mod, "registry", _SurrogateRegistry())

    pipe = pipeline_mod.Pipeline(object())
    reached: list[tuple] = []

    async def _sentinel(*args, **_kwargs):
        reached.append(args)
        raise _NextStageReached

    monkeypatch.setattr(pipe, "_analyze_document", _sentinel)
    file_path = tmp_path / "t.md"
    file_path.write_text("# T", encoding="utf-8")

    await pipe.process_file(SURROGATE_DOC_ID, file_path, "t.md", "markdown")

    assert reached, "提取文本带代理项时没能走到分析阶段"
    assert reached[0][0] == SURROGATE_TEXT


async def test_reindex_document_hash_survives_surrogates(monkeypatch):
    doc = SimpleNamespace(
        id=SURROGATE_DOC_ID,
        title="t.md",
        file_type="markdown",
        version_of=None,
        version_number=1,
        is_current=True,
        file_path=None,
    )
    monkeypatch.setattr(
        pipeline_mod, "async_session_factory", lambda: _StubSession(doc)
    )

    pipe = pipeline_mod.Pipeline(object())
    reached: list[tuple] = []

    async def _sentinel(*args, **_kwargs):
        reached.append(args)
        raise _NextStageReached

    monkeypatch.setattr(pipe, "_analyze_document", _sentinel)

    await pipe.reindex_document(SURROGATE_DOC_ID, SURROGATE_TEXT)

    assert reached, "编辑文本带代理项时没能走到分析阶段"
    assert reached[0][0] == SURROGATE_TEXT


async def test_ingest_hash_survives_surrogates(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(backend_mod, "registry", _SurrogateRegistry())
    monkeypatch.setattr(backend_mod, "async_session_factory", _StubSession)

    backend = backend_mod.GraphRAGBackend(SimpleNamespace(), _NoopPipeline())

    ref, task = await backend._ingest_one(IngestSource(name="a.md", data=b"# A"))
    await task
    await asyncio.sleep(0)

    assert ref.title == "a.md"
    assert ref.status == "pending"


async def test_edit_content_sanitizes_surrogates(monkeypatch, tmp_path):
    """编辑内容不经过 registry，代理项必须在 edit_document 入口归一化。"""
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    document = SimpleNamespace(
        id=SURROGATE_DOC_ID,
        title="week.md",
        file_type="markdown",
        status="indexed",
        overview="old overview",
        error_msg="old error",
        raw_text="old text",
        content_hash="old-hash",
        bank_id="default-team",
        tags=[],
        version_group=SURROGATE_DOC_ID,
        version_number=1,
        version_of=None,
        is_current=True,
    )
    session = _StubSession(document)
    monkeypatch.setattr(backend_mod, "async_session_factory", lambda: session)
    monkeypatch.setattr(
        backend_mod, "_remove_upload_directory", lambda *_args, **_kwargs: None
    )

    backend = backend_mod.GraphRAGBackend(SimpleNamespace(), _NoopPipeline())

    result = await backend.edit_content(str(SURROGATE_DOC_ID), SURROGATE_TEXT)
    await asyncio.sleep(0)

    assert len(session.added) == 1
    new_doc = session.added[0]
    assert new_doc.raw_text == "alpha�beta"
    assert len(new_doc.raw_text) == len(SURROGATE_TEXT)  # 偏移不变
    assert new_doc.raw_text.encode("utf-8")  # 落库与落盘都不再抛
    assert Path(new_doc.file_path).read_text(encoding="utf-8") == "alpha�beta"
    assert result.id == str(new_doc.id)
