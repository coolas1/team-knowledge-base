from pathlib import Path

from src.pipeline.extractors.base import BaseExtractor
from src.pipeline.extractors.docx import DocxExtractor
from src.pipeline.extractors.image import ImageExtractor
from src.pipeline.extractors.markdown import MarkdownExtractor
from src.pipeline.extractors.pdf import PDFExtractor
from src.pipeline.extractors.pptx import PPTXExtractor
from src.pipeline.extractors.tabular import TabularExtractor


class ExtractorRegistry:
    """按文件扩展名路由到对应的 Extractor。"""

    def __init__(self) -> None:
        self._extractors: list[BaseExtractor] = [
            MarkdownExtractor(),
            PDFExtractor(),
            DocxExtractor(),
            PPTXExtractor(),
            ImageExtractor(),
            TabularExtractor(),
        ]
        # 扩展名 → extractor 的映射缓存
        self._ext_map: dict[str, BaseExtractor] = {}
        for ext in self._extractors:
            for supported_ext in ext.SUPPORTED_EXTENSIONS:
                self._ext_map[supported_ext.lower()] = ext

    def get_extractor(self, file_path: Path) -> BaseExtractor:
        """根据文件扩展名返回对应的 Extractor。

        Raises:
            ValueError: 不支持的文件类型
        """
        ext = file_path.suffix.lower()
        extractor = self._ext_map.get(ext)
        if extractor is None:
            raise ValueError(f"不支持的文件类型: {ext}")
        return extractor

    def extract(self, file_path: Path) -> str:
        """快捷方法：获取 extractor 并提取文本。"""
        return self.get_extractor(file_path).extract(file_path)

    @staticmethod
    def guess_file_type(file_path: Path) -> str:
        """根据扩展名推断 file_type 字段值。"""
        ext = file_path.suffix.lower()
        type_map = {
            ".md": "markdown",
            ".markdown": "markdown",
            ".txt": "markdown",
            ".pdf": "pdf",
            ".docx": "docx",
            ".pptx": "pptx",
            ".png": "image",
            ".jpg": "image",
            ".jpeg": "image",
            ".tiff": "image",
            ".bmp": "image",
            ".webp": "image",
            ".csv": "csv",
            ".xlsx": "xlsx",
            ".xls": "xlsx",
        }
        return type_map.get(ext, ext.lstrip("."))


# 全局单例
registry = ExtractorRegistry()
