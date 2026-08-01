"""Adapter for the target project's existing file extractors."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from src.engine.components.extractors.registry import ExtractorRegistry, registry


class ProjectSourceExtractor:
    """Reuse TKB's current PDF/DOCX/PPTX/Markdown/image extraction stack."""

    def __init__(self, extractor_registry: ExtractorRegistry = registry) -> None:
        self._registry = extractor_registry

    async def extract(
        self, name: str, data: bytes, path: str | None
    ) -> tuple[str, str]:
        source_path = Path(path) if path else None
        if source_path is not None and not data:
            if not source_path.is_file():
                raise ValueError(f"文件不存在: {source_path}")
            text = await asyncio.to_thread(self._registry.extract, source_path)
            return text, self._registry.guess_file_type(Path(name))

        if not data:
            raise ValueError("上传内容为空")

        suffix = Path(name).suffix
        with tempfile.TemporaryDirectory(prefix="tkb-hindsight-") as temp_dir:
            temp_path = Path(temp_dir) / f"source{suffix}"
            await asyncio.to_thread(temp_path.write_bytes, data)
            text = await asyncio.to_thread(self._registry.extract, temp_path)
        return text, self._registry.guess_file_type(Path(name))
