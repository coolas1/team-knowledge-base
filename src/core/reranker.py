"""Reranker 服务：支持 DashScope API 和本地 sentence-transformers 双 provider。

通过 model_config.yaml 的 gatekeeper.provider 切换：
- dashscope: 调用 DashScope gte-rerank API（无需本地 GPU）
- local: 使用本地 sentence-transformers CrossEncoder（需要 PyTorch 环境）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

from src.db.config import settings

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_config.yaml"

# 不再使用硬编码禁用开关，改为 provider 配置驱动
RERANKER_DISABLED: bool = False


def _load_gatekeeper_config() -> dict:
    """从 model_config.yaml 读取 gatekeeper 配置。"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            return cfg.get("gatekeeper", {})
    return {}


class Reranker:
    """Reranker 客户端，支持 dashscope API 和 local sentence-transformers。"""

    def __init__(self) -> None:
        self._cfg = _load_gatekeeper_config()
        self._provider = self._cfg.get("provider", "dashscope")
        self._model = self._cfg.get("model", "gte-rerank")
        self._threshold = self._cfg.get("threshold", 0.01)
        self._top_n = self._cfg.get("top_n", 15)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reranker")

        if self._provider == "dashscope":
            # DashScope API 配置（qwen3-rerank 使用 compatible-api 端点）
            self._base_url = self._cfg.get(
                "base_url", "https://dashscope.aliyuncs.com/compatible-api/v1"
            ).rstrip("/")
            self._api_key = self._cfg.get("api_key", "") or settings.llm_api_key
            logger.info(f"Reranker 初始化: provider=dashscope | model={self._model}")
        elif self._provider == "local":
            # 本地 sentence-transformers
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            try:
                from sentence_transformers import CrossEncoder
                self._local_model = CrossEncoder(self._model)
                logger.info(f"Reranker 初始化: provider=local | model={self._model} ✓")
            except Exception as e:
                logger.warning(f"Reranker 本地模型加载失败，降级为 noop: {e}")
                self._provider = "noop"
                self._local_model = None
        else:
            logger.warning(f"Reranker provider 未知: {self._provider}，降级为 noop")
            self._provider = "noop"

    async def areank(self, query: str, texts: list[str]) -> list[float]:
        """对 (query, text) 对打分（异步版本）。

        Returns:
            与 texts 等长的分数列表，按原始顺序排列。
        """
        if not texts:
            return []

        if self._provider == "dashscope":
            return await self._rerank_dashscope(query, texts)
        elif self._provider == "local":
            return await self._rerank_local(query, texts)
        else:
            # noop: 返回零分
            return [0.0] * len(texts)

    # ── DashScope Reranker API ───────────────────────────────────

    async def _rerank_dashscope(self, query: str, texts: list[str]) -> list[float]:
        """调用 DashScope qwen3-rerank API 打分。

        API: POST {base_url}/reranks
        请求格式（扁平）: {model, query, documents, top_n}
        响应格式: {results: [{index, relevance_score}], ...}
        """
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/reranks",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "query": query,
                        "documents": texts,
                        "top_n": len(texts),  # 返回所有分数，由上层过滤
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"Reranker dashscope 完成: {len(texts)} 文档 | 耗时 {elapsed_ms:.0f}ms"
            )

            # qwen3-rerank 响应: results 在顶层（无 output 包裹）
            results = data.get("results", [])
            # 按 index 还原到原始顺序
            scores = [0.0] * len(texts)
            for r in results:
                idx = r["index"]
                if 0 <= idx < len(texts):
                    scores[idx] = float(r["relevance_score"])

            return scores

        except Exception as e:
            logger.warning(
                f"Reranker dashscope 调用失败，降级返回零分: {type(e).__name__}: {e}"
            )
            return [0.0] * len(texts)

    # ── Local sentence-transformers ────────────────────────────────

    async def _rerank_local(self, query: str, texts: list[str]) -> list[float]:
        """本地 CrossEncoder predict（在线程池中运行）。"""
        t0 = time.monotonic()
        pairs = [(query, text) for text in texts]
        loop = asyncio.get_running_loop()
        try:
            scores = await loop.run_in_executor(
                self._executor, self._predict_local, pairs
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"Reranker local 完成: {len(texts)} 文档 | 耗时 {elapsed_ms:.0f}ms"
            )
            return [float(s) for s in scores]
        except Exception as e:
            logger.warning(
                f"Reranker local predict 失败，降级返回零分: {type(e).__name__}: {e}"
            )
            return [0.0] * len(texts)

    def _predict_local(self, pairs: list[tuple[str, str]]) -> list[float]:
        """同步调用 CrossEncoder.predict。"""
        scores = self._local_model.predict(pairs)
        return [float(s) for s in scores]


# 全局单例（懒加载）
_reranker_instance: Reranker | None = None


def get_reranker() -> Reranker:
    """获取 Reranker 单例（懒加载）。"""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = Reranker()
    return _reranker_instance
