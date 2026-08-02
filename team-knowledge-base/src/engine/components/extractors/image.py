from pathlib import Path

from PIL import Image

import pytesseract

from src.engine.components.extractors.base import BaseExtractor


class ImageExtractor(BaseExtractor):
    """通过 OCR (pytesseract) 提取图片中的文字。"""

    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
            return text.strip()
        except pytesseract.TesseractNotFoundError:
            raise ValueError(
                "Tesseract OCR 未安装。请运行: brew install tesseract tesseract-lang"
            )
        except Exception as e:
            raise ValueError(f"图片 OCR 失败: {file_path} — {e}") from e
