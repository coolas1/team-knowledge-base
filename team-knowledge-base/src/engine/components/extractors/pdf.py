from pathlib import Path

from pypdf import PdfReader

from src.engine.components.extractors.base import BaseExtractor


class PDFExtractor(BaseExtractor):
    """提取 PDF 文件文本内容。"""

    SUPPORTED_EXTENSIONS = {".pdf"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            reader = PdfReader(str(file_path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
        except Exception as e:
            raise ValueError(f"PDF 解析失败: {file_path} — {e}") from e
