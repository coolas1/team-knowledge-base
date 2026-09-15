"""提取文本的代理项归一化规则（registry 收敛点）。"""

import logging
from pathlib import Path

import pytest

from src.engine.components.extractors.registry import ExtractorRegistry, registry
from src.engine.components.extractors.sanitize import (
    sanitize_surrogates,
    sha256_of_text,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SURROGATE_PDF = FIXTURES / "surrogate_cmap.pdf"


def test_replaces_lone_surrogate_with_one_ufffd():
    result = sanitize_surrogates("A\udbc3B")

    assert result.text == "A�B"
    assert result.replacements == 1
    assert result.positions == (1,)


def test_replacement_preserves_length_and_offsets():
    text = "alpha\udbc3beta"
    result = sanitize_surrogates(text)

    assert len(result.text) == len(text)
    assert result.text.index("beta") == text.index("beta")
    assert result.text[0] == "a" and result.text[-1] == "a"


def test_text_without_surrogates_is_returned_unchanged():
    text = "正常文本 with emoji 😀 and CJK"

    result = sanitize_surrogates(text)

    assert result.text is text
    assert result.positions == ()


def test_sanitized_text_is_utf8_encodable():
    assert sanitize_surrogates("A\udbc3B\udebcC").text.encode("utf-8")


def test_registry_sanitizes_any_extractor_output(monkeypatch):
    """任意格式的 extractor 输出都在 registry 这一层被归一化。"""
    extractor = registry.get_extractor(FIXTURES / "sample.md")
    monkeypatch.setattr(extractor, "extract", lambda _path: "A\udbc3B")

    assert registry.extract(FIXTURES / "sample.md") == "A�B"


def test_registry_logs_document_and_replacement_count(monkeypatch, caplog):
    extractor = registry.get_extractor(FIXTURES / "sample.md")
    monkeypatch.setattr(extractor, "extract", lambda _path: "x" * 10 + "\udbc3" + "y")

    with caplog.at_level(
        logging.WARNING, logger="src.engine.components.extractors.registry"
    ):
        registry.extract(FIXTURES / "sample.md")

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "sample.md" in message
    assert "替换数=1" in message
    assert "位置=[10]" in message


def test_registry_logs_nothing_for_clean_text(caplog):
    with caplog.at_level(
        logging.WARNING, logger="src.engine.components.extractors.registry"
    ):
        registry.extract(FIXTURES / "sample.md")

    assert caplog.records == []


def test_registry_keeps_its_default_extractors():
    """归一化只是包一层，不能改变路由行为。"""
    assert isinstance(registry, ExtractorRegistry)
    assert registry.extract(FIXTURES / "sample.md").startswith("#")


# ── 真实缺陷的极小复现：ToUnicode 映射到孤立代理项的 PDF ──────────────


def test_surrogate_cmap_fixture_is_minimal():
    assert SURROGATE_PDF.stat().st_size < 2048


def test_raw_pdf_extraction_reproduces_the_defect():
    """fixture 必须真的复现缺陷，否则后面的断言毫无意义。"""
    from src.engine.components.extractors.pdf import PDFExtractor

    raw = PDFExtractor().extract(SURROGATE_PDF)

    assert any(0xD800 <= ord(char) <= 0xDFFF for char in raw)
    with pytest.raises(UnicodeEncodeError):
        raw.encode("utf-8")


def test_surrogate_cmap_pdf_extracts_to_replacement_character():
    text = registry.extract(SURROGATE_PDF)

    assert text == "�"
    assert not any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def test_surrogate_cmap_pdf_content_hash_succeeds():
    """入库路径最先失败的正是内容哈希这一步。"""
    text = registry.extract(SURROGATE_PDF)

    assert sha256_of_text(text)
    assert text.encode("utf-8")
