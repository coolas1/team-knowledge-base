"""Embedding 服务：支持 DashScope (text-embedding-v3) 和 Ollama 双 provider。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
import yaml

from src.db.config import settings

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_config.yaml"


def _load_embedding_config() -> dict:
    """从 model_config.yaml 读取 embedding 配置。"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg.get("embedding", {})
    return {}


class Embedder:
    """Embedding 客户端，支持 dashscope 和 ollama 两种 provider。

    - dashscope: 调用 DashScope OpenAI 兼容接口 text-embedding-v3/v4
    - ollama: 调用本地 Ollama /api/embed
    """

    def __init__(self) -> None:
        cfg = _load_embedding_config()
        self._provider = cfg.get("provider", "ollama")
        self._model = cfg.get("model", "nomic-embed-text")
        self._dimensions = cfg.get("dimensions", 1024)

        if self._provider == "dashscope":
            self._base_url = cfg.get(
                "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).rstrip("/")
            self._api_key = cfg.get("api_key", "") or settings.llm_api_key
        else:
            self._base_url = cfg.get("base_url", settings.ollama_base_url).rstrip("/")

        logger.info(
            f"Embedder 初始化: provider={self._provider} | model={self._model} | "
            f"dimensions={self._dimensions}"
        )

    async def embed_text(self, text: str) -> list[float]:
        """嵌入单条文本。"""
        if self._provider == "dashscope":
            return await self._embed_dashscope(text)
        return await self._embed_ollama(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入多条文本。"""
        if not texts:
            return []
        t0 = time.monotonic()

        if self._provider == "dashscope":
            embeddings = await self._embed_batch_dashscope(texts)
        else:
            embeddings = await self._embed_batch_ollama(texts)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            f"Embedding 批量完成: {len(texts)} 条 | 耗时 {elapsed_ms:.0f}ms | "
            f"provider={self._provider} model={self._model}"
        )
        return embeddings

    # ── DashScope ────────────────────────────────────────────────

    async def _embed_dashscope(self, text: str) -> list[float]:
        """DashScope OpenAI 兼容接口嵌入单条。"""
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "input": text,
                    "dimensions": self._dimensions,
                    "encoding_format": "float",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"Embedding 单条完成(dashscope): 耗时 {elapsed_ms:.0f}ms")
        return data["data"][0]["embedding"]

    async def _embed_batch_dashscope(self, texts: list[str]) -> list[list[float]]:
        """DashScope 批量嵌入（单次最多 10 条，自动分批）。"""
        BATCH_SIZE = 10
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": batch,
                        "dimensions": self._dimensions,
                        "encoding_format": "float",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            # 按 index 排序确保顺序正确
            items = sorted(data["data"], key=lambda x: x["index"])
            all_embeddings.extend(item["embedding"] for item in items)

        return all_embeddings

    # ── Ollama ───────────────────────────────────────────────────

    async def _embed_ollama(self, text: str) -> list[float]:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": text},
            )
            resp.raise_for_status()
            data = resp.json()
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"Embedding 单条完成(ollama): 耗时 {elapsed_ms:.0f}ms")
        return data["embeddings"][0]

    async def _embed_batch_ollama(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        return data["embeddings"]


# 全局单例
embedder = Embedder()
