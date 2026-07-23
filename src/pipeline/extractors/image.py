from pathlib import Path
import shutil

from PIL import Image

import pytesseract

from src.pipeline.extractors.base import BaseExtractor


class ImageExtractor(BaseExtractor):
    """通过 OCR (pytesseract) 提取图片中的文字。"""

    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

    @staticmethod
    def _configure_tesseract() -> str:
        """Locate Tesseract and use every installed benchmark language."""
        if shutil.which("tesseract") is None:
            windows_executable = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
            if windows_executable.exists():
                pytesseract.pytesseract.tesseract_cmd = str(windows_executable)
        available = set(pytesseract.get_languages(config=""))
        languages = [lang for lang in ("chi_sim", "jpn", "eng") if lang in available]
        if not languages:
            raise pytesseract.TesseractNotFoundError()
        return "+".join(languages)

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            image = Image.open(file_path)
            text = pytesseract.image_to_string(
                image, lang=self._configure_tesseract()
            )
            return text.strip()
        except pytesseract.TesseractNotFoundError:
            raise ValueError(
                "Tesseract OCR 未安装或没有可用语言包；请安装 tesseract 与至少一个 traineddata"
            )
        except Exception as e:
            raise ValueError(f"图片 OCR 失败: {file_path} — {e}") from e
