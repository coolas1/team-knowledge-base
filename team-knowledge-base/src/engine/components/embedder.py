"""Embedding 服务：支持 Ollama 和 ARK (OpenAI-compatible) 两种后端。

通过 .env 中 EMBED_PROVIDER 切换：
  - ollama: 本地 Ollama /api/embed（默认 nomic-embed-text 768d）
  - ark:    火山方舟 /embeddings/multimodal（doubao-embedding-vision 系列，2048d 原始输出，
            客户端 MRL 截取 + L2 归一化到 embed_dim）
"""

from __future__ import annotations

import math

import httpx

from config.settings import settings


def _slice_and_normalize(vec: list[float], dim: int) -> list[float]:
    """MRL 截取前 dim 维 + L2 归一化。"""
    if len(vec) == dim:
        return vec
    sliced = vec[:dim]
    norm = math.sqrt(sum(v * v for v in sliced))
    if norm == 0:
        return sliced
    return [v / norm for v in sliced]


class Embedder:
    """双后端 embedding 客户端（ollama / ark）。"""

    def __init__(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        dim: int | None = None,
    ) -> None:
        self._provider = (provider or settings.embed_provider).lower()
        self._dim = dim or settings.embed_dim

        if self._provider == "ark":
            self._base_url = (base_url or settings.embed_base_url or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
            self._model = model or settings.embed_model or "doubao-embedding-vision-251215"
            self._api_key = api_key or settings.embed_api_key
            if not self._api_key:
                raise ValueError("embed_provider=ark 需要设置 EMBED_API_KEY")
        else:
            # ollama (default)
            self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
            self._model = model or settings.embed_model or "nomic-embed-text"
            self._api_key = ""

    # ── public API ─────────────────────────────────────────────────────

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单条文本，返回 embed_dim 维向量。"""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入多条文本。"""
        if not texts:
            return []
        if self._provider == "ark":
            return await self._embed_batch_ark(texts)
        return await self._embed_batch_ollama(texts)

    # ── Ollama backend ─────────────────────────────────────────────────

    async def _embed_batch_ollama(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            return resp.json()["embeddings"]

    # ── ARK / OpenAI-compatible backend ────────────────────────────────

    async def _embed_batch_ark(self, texts: list[str]) -> list[list[float]]:
        """火山方舟 /embeddings/multimodal 端点。

        该端点单请求可接收多个 input item，返回 2048d 向量。
        客户端 MRL 截取前 embed_dim 维 + L2 归一化。
        """
        input_items = [{"type": "text", "text": t} for t in texts]
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings/multimodal",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._api_key}",
                },
                json={"model": self._model, "input": input_items},
            )
            resp.raise_for_status()
            data = resp.json()
            # 响应格式: {"data": [{"embedding": [...], "index": 0}, ...]} 或 {"data": {"embedding": [...]}}
            raw = data["data"]
            items = raw if isinstance(raw, list) else [raw]
            items = sorted(items, key=lambda x: x.get("index", 0))
            return [_slice_and_normalize(item["embedding"], self._dim) for item in items]


# 全局单例
embedder = Embedder()
