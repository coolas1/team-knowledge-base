import re
import sys
from pathlib import Path

from PIL import Image

import pytesseract

from src.engine.components.extractors.base import BaseExtractor

# OCR 质量门：有效字符（字母数字 + CJK）低于该阈值视为没有可读文本，
# 整图判定失败而不是入库为垃圾文档。保守常量，后续可按 bench 语料调优。
MIN_MEANINGFUL_CHARS = 8

_MEANINGFUL_CHARS = re.compile(r"[0-9A-Za-z一-鿿]")


def _ocr_install_hint() -> str:
    """按运行平台给出 Tesseract 安装指引（Linux 部署不应看到 brew）。"""
    if sys.platform == "darwin":
        return "brew install tesseract tesseract-lang"
    return (
        "安装 tesseract-ocr 与中文语言包（Debian/Ubuntu: "
        "apt install tesseract-ocr tesseract-ocr-chi-sim）"
    )


class ImageExtractor(BaseExtractor):
    """通过 OCR (pytesseract) 提取图片中的文字。"""

    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image, lang="chi_sim+eng").strip()
        except pytesseract.TesseractNotFoundError:
            raise ValueError(f"Tesseract OCR 未安装。请运行: {_ocr_install_hint()}")
        except Exception as e:
            raise ValueError(f"图片 OCR 失败: {file_path} — {e}") from e
        meaningful = len(_MEANINGFUL_CHARS.findall(text))
        if meaningful < MIN_MEANINGFUL_CHARS:
            raise ValueError(
                f"OCR 未提取到有效文本（有效字符 {meaningful} 个）："
                "请确认图片清晰且包含可读文字，OCR 质量不足的图片不会被入库。"
            )
        return text
