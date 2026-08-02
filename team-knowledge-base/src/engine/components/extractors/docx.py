from pathlib import Path

from docx import Document as DocxDocument

from src.engine.components.extractors.base import BaseExtractor


class DocxExtractor(BaseExtractor):
    """提取 Word (.docx) 文件文本内容。"""

    SUPPORTED_EXTENSIONS = {".docx"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            doc = DocxDocument(str(file_path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX 解析失败: {file_path} — {e}") from e
