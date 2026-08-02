from pathlib import Path

import pytest

from src.engine.components.extractors.registry import ExtractorRegistry, registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_registry_is_singleton():
    assert isinstance(registry, ExtractorRegistry)


def test_extract_markdown():
    text = registry.extract(FIXTURES / "sample.md")
    assert "Alice works at Acme" in text


def test_extract_txt_treated_as_markdown():
    text = registry.extract(FIXTURES / "sample.txt")
    assert "plain text file" in text


def test_guess_file_type():
    assert ExtractorRegistry.guess_file_type(Path("a.md")) == "markdown"
    assert ExtractorRegistry.guess_file_type(Path("a.pdf")) == "pdf"
    assert ExtractorRegistry.guess_file_type(Path("a.png")) == "image"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="不支持的文件类型"):
        registry.get_extractor(Path("a.xyz"))
