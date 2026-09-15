"""畸形 trailer 的修复：只在原始字节解析失败之后才重试。"""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader
from pypdf.errors import PdfStreamError

from src.engine.components.extractors import pdf as pdf_mod
from src.engine.components.extractors.pdf import PDFExtractor
from src.engine.graphrag import backend as backend_mod
from src.engine.interface import IngestSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FLATTENED = FIXTURES / "flattened_trailer.pdf"
WELL_FORMED = FIXTURES / "surrogate_cmap.pdf"

EXPECTED_TEXT = "Flattened trailer fixture"


def _unflatten(data: bytes) -> bytes:
    """把 startxref / 偏移 / %%EOF 还原成各自一行。

    刻意不用生产代码里的那条正则，否则对照就变成了自证。
    """
    return data.replace(b"startxref ", b"startxref\r\n").replace(b" %%EOF", b"\r\n%%EOF")


class _RecordingReader:
    """记录每次 PdfReader 构造的结局，用来断言重试时机。"""

    def __init__(self, real):
        self._real = real
        self.outcomes: list[str] = []

    def __call__(self, stream):
        try:
            reader = self._real(stream)
        except Exception as exc:
            self.outcomes.append(type(exc).__name__)
            raise
        self.outcomes.append("ok")
        return reader


def test_fixture_reproduces_the_defect():
    """fixture 必须真的打不开，否则修复测试毫无意义。"""
    with pytest.raises(PdfStreamError):
        PdfReader(BytesIO(FLATTENED.read_bytes()))


def test_flattened_trailer_extracts():
    assert PDFExtractor().extract(FLATTENED) == EXPECTED_TEXT


def test_unflattened_bytes_extract_to_identical_text(tmp_path):
    plain = tmp_path / "plain.pdf"
    plain.write_bytes(_unflatten(FLATTENED.read_bytes()))

    assert PDFExtractor().extract(plain) == PDFExtractor().extract(FLATTENED)


def test_retry_only_follows_a_failed_first_attempt(monkeypatch):
    recorder = _RecordingReader(pdf_mod.PdfReader)
    monkeypatch.setattr(pdf_mod, "PdfReader", recorder)

    assert PDFExtractor().extract(FLATTENED) == EXPECTED_TEXT
    assert recorder.outcomes == ["PdfStreamError", "ok"]


def test_well_formed_pdf_is_opened_exactly_once(monkeypatch):
    """好文件不该被改动字节，也不该多解析一次。"""
    recorder = _RecordingReader(pdf_mod.PdfReader)
    monkeypatch.setattr(pdf_mod, "PdfReader", recorder)

    PDFExtractor().extract(WELL_FORMED)

    assert recorder.outcomes == ["ok"]


def _truncated_pdf() -> bytes:
    plain = _unflatten(FLATTENED.read_bytes())
    return plain[: len(plain) // 2]


def test_truncated_pdf_still_fails_with_the_underlying_reason(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(_truncated_pdf())

    with pytest.raises(ValueError) as excinfo:
        PDFExtractor().extract(broken)

    message = str(excinfo.value)
    assert "PDF 解析失败" in message
    assert "broken.pdf" in message
    # 底层原因必须带出来，而不是被修复逻辑吞成一句笼统的"损坏"
    assert str(excinfo.value.__cause__)


async def test_unrecoverable_pdf_creates_no_document(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_mod, "UPLOAD_DIR", tmp_path / "uploads")
    added: list = []

    class _RecordingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, obj):
            added.append(obj)

        async def execute(self, *_args):
            return None

        async def commit(self):
            return None

    monkeypatch.setattr(backend_mod, "async_session_factory", _RecordingSession)
    backend = backend_mod.GraphRAGBackend(SimpleNamespace(), SimpleNamespace())

    with pytest.raises(ValueError, match="PDF 解析失败"):
        await backend._ingest_one(
            IngestSource(name="broken.pdf", data=_truncated_pdf())
        )

    assert added == []
