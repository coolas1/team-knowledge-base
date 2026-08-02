"""Embedding 服务：通过 Ollama HTTP API 调用 nomic-embed-text 模型。"""

from __future__ import annotations

import httpx

from config.settings import settings


class Embedder:
    """Ollama embedding 客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or "nomic-embed-text"

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单条文本。

        Returns:
            768 维向量 (nomic-embed-text)
        """
        return await self._embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入多条文本。

        Ollama 的 /api/embed 支持批量输入。
        """
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"]

    async def _embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["embeddings"][0]


# 全局单例
embedder = Embedder()
