import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfStreamError

from src.engine.components.extractors.base import BaseExtractor

# 源站点的后处理会把 startxref、交叉引用偏移和 %%EOF 挤到同一行，pypdf 的
# 反向 EOF 扫描因此失效。合法 PDF 里这三者天然分行，所以命中即损坏证据。
# 只在首次解析已经失败后才跑，误伤二进制流的风险可以接受。
_FLATTENED_TRAILER = re.compile(rb"startxref\s+(\d+)\s+%%EOF")
_TRAILER_REPAIR = rb"startxref\r\n\1\r\n%%EOF"


class PDFExtractor(BaseExtractor):
    """提取 PDF 文件文本内容。"""

    SUPPORTED_EXTENSIONS = {".pdf"}

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            reader = self._open(file_path.read_bytes())
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n\n".join(pages)
        except Exception as e:
            raise ValueError(f"PDF 解析失败: {file_path} — {e}") from e

    @staticmethod
    def _open(data: bytes) -> PdfReader:
        """先按原始字节解析；只有 PdfStreamError 才修复 trailer 重试一次。

        修复是重试而非预处理：绝大多数 PDF 是好的，不该为它们改动字节。
        """
        try:
            return PdfReader(BytesIO(data))
        except PdfStreamError:
            repaired = _FLATTENED_TRAILER.sub(_TRAILER_REPAIR, data)
            if repaired == data:
                # 没有可修的 trailer，重试同样的字节没有意义。
                raise
            return PdfReader(BytesIO(repaired))
