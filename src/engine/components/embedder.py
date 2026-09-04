"""Embedding 服务：通过 OpenAI 兼容 /v1/embeddings API 生成向量。"""

from __future__ import annotations

import httpx

from config.settings import settings
from src.engine.components.store.models import EMBEDDING_DIM


class Embedder:
    """OpenAI 兼容 embedding 客户端（POST {base_url}/embeddings）。"""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = (base_url or settings.embedding.base_url).rstrip("/")
        self._model = model or settings.embedding.model
        self._api_key = (
            settings.embedding.api_key if api_key is None else api_key
        )

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单条文本。

        Returns:
            768 维向量 (EMBEDDING_DIM)
        """
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入多条文本（OpenAI /v1/embeddings 支持批量 input）。

        响应按 index 映射回输入顺序（规范不保证响应顺序），并对维度
        做校验：非 EMBEDDING_DIM 直接报错，而不是写入时才失败。
        """
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers=headers,
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        vectors: dict[int, list[float]] = {}
        for item in data["data"]:
            idx = item["index"]
            if not 0 <= idx < len(texts):
                raise ValueError(
                    f"embedding API returned index {idx} out of range "
                    f"for {len(texts)} inputs"
                )
            vectors[idx] = self._checked(item["embedding"])
        if len(vectors) != len(texts):
            missing = [i for i in range(len(texts)) if i not in vectors]
            raise ValueError(
                f"embedding API returned {len(data['data'])} vectors for "
                f"{len(texts)} inputs (missing indices {missing})"
            )
        return [vectors[i] for i in range(len(texts))]

    def _checked(self, vector: list[float]) -> list[float]:
        """维度校验：模型输出必须等于存储宽度 EMBEDDING_DIM。"""
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding model {self._model!r} returned "
                f"{len(vector)}-dim vectors; this build stores "
                f"{EMBEDDING_DIM}-dim (EMBEDDING_DIM in store/models.py)"
            )
        return vector


# 全局单例
embedder = Embedder()