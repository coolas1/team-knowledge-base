from pathlib import Path

from src.engine.components.extractors.base import BaseExtractor


class MarkdownExtractor(BaseExtractor):
    """提取 Markdown (.md) 和纯文本 (.txt) 文件。"""

    SUPPORTED_EXTENSIONS = {".md", ".txt", ".markdown"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        return file_path.read_text(encoding="utf-8")
