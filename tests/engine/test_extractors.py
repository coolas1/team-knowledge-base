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


def test_extract_csv_treated_as_text():
    text = registry.extract(FIXTURES / "sample.csv")
    assert "Alice,Engineer,Acme" in text


def test_csv_uses_markdown_extractor():
    from src.engine.components.extractors.markdown import MarkdownExtractor

    assert isinstance(registry.get_extractor(Path("data.csv")), MarkdownExtractor)


def test_guess_file_type_csv():
    assert ExtractorRegistry.guess_file_type(Path("a.csv")) == "csv"


def test_guess_file_type():
    assert ExtractorRegistry.guess_file_type(Path("a.md")) == "markdown"
    assert ExtractorRegistry.guess_file_type(Path("a.pdf")) == "pdf"
    assert ExtractorRegistry.guess_file_type(Path("a.png")) == "image"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="不支持的文件类型"):
        registry.get_extractor(Path("a.xyz"))


def _blank_png(path):
    from PIL import Image as PILImage

    PILImage.new("RGB", (16, 16), "white").save(path)
    return path


def test_image_ocr_quality_gate_rejects_textless_image(monkeypatch, tmp_path):
    from src.engine.components.extractors import image as image_mod

    monkeypatch.setattr(
        image_mod.pytesseract, "image_to_string", lambda _img, lang=None: "  \n *** \n"
    )
    path = _blank_png(tmp_path / "blank.png")

    with pytest.raises(ValueError, match="OCR 未提取到有效文本"):
        registry.extract(path)


def test_image_ocr_quality_gate_passes_adequate_text(monkeypatch, tmp_path):
    from src.engine.components.extractors import image as image_mod

    monkeypatch.setattr(
        image_mod.pytesseract,
        "image_to_string",
        lambda _img, lang=None: "Acme 园区在 A 栋 3 层。",
    )
    path = _blank_png(tmp_path / "text.png")

    text = registry.extract(path)

    assert text == "Acme 园区在 A 栋 3 层。"


def test_image_ocr_missing_install_hint_is_platform_aware(monkeypatch, tmp_path):
    from src.engine.components.extractors import image as image_mod

    def _not_installed(_img, lang=None):
        raise image_mod.pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(image_mod.pytesseract, "image_to_string", _not_installed)
    path = _blank_png(tmp_path / "any.png")

    with monkeypatch.context() as m:
        m.setattr(image_mod.sys, "platform", "linux")
        with pytest.raises(ValueError, match="tesseract-ocr") as excinfo:
            registry.extract(path)
        assert "brew" not in str(excinfo.value)

    with monkeypatch.context() as m:
        m.setattr(image_mod.sys, "platform", "darwin")
        with pytest.raises(ValueError, match="brew install tesseract"):
            registry.extract(path)
