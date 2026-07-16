"""Reranker 服务：使用 bge-reranker-v2-m3 交叉编码器对 (query, text) 对打分。

注意：CrossEncoder.predict() 在当前 Windows + PyTorch 环境下存在 C 扩展级崩溃，
无法被 Python 异常处理捕获。因此默认禁用 predict 调用，降级返回向量分数。
待环境修复后可将 RERANKER_DISABLED 改为 False 重新启用。
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

# 优先使用本地缓存，避免每次加载都尝试联网检查
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

logger = logging.getLogger(__name__)

# ── Reranker 全局开关 ─────────────────────────────────────────
# True = 跳过 CrossEncoder predict，降级返回零分（避免 C 扩展崩溃）
# False = 正常调用 CrossEncoder predict（需要 PyTorch 环境正常）
RERANKER_DISABLED: bool = True

# 默认模型配置
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class Reranker:
    """基于 CrossEncoder 的重排序模型。

    对 (query, text) 文本对进行相关性打分，
    用于检索守门层替换原有的 cosine similarity。
    当 RERANKER_DISABLED=True 时跳过模型加载和 predict。
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reranker")
        if RERANKER_DISABLED:
            logger.warning("Reranker 已禁用（RERANKER_DISABLED=True），跳过模型加载")
            self.model = None
        else:
            logger.info("加载 Reranker 模型: %s ...", model_name)
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name)
            logger.info("Reranker 模型加载完成 ✓")

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """对 (query, text) 对打分（同步版本）。"""
        if not texts:
            return []
        if RERANKER_DISABLED or self.model is None:
            return [0.0] * len(texts)
        pairs = [(query, text) for text in texts]
        scores = self.model.predict(pairs)
        return [float(s) for s in scores]

    async def areank(self, query: str, texts: list[str]) -> list[float]:
        """对 (query, text) 对打分（异步版本）。

        RERANKER_DISABLED 时直接返回零分，不调用 predict。
        """
        if not texts:
            return []
        if RERANKER_DISABLED or self.model is None:
            logger.debug("Reranker 已禁用，返回零分")
            return [0.0] * len(texts)
        pairs = [(query, text) for text in texts]
        loop = asyncio.get_running_loop()
        try:
            scores = await loop.run_in_executor(
                self._executor, self._predict_safe, pairs
            )
            return [float(s) for s in scores]
        except Exception as e:
            logger.warning(f"Reranker predict 失败，降级返回零分: {type(e).__name__}: {e}")
            return [0.0] * len(texts)

    @staticmethod
    def _predict_safe(pairs: list[tuple[str, str]]) -> list[float]:
        """安全包装 predict。"""
        try:
            from sentence_transformers import CrossEncoder
            model = CrossEncoder(DEFAULT_MODEL)
            scores = model.predict(pairs)
            return [float(s) for s in scores]
        except Exception as e:
            logger.warning(f"Reranker _predict_safe 失败: {type(e).__name__}: {e}")
            return [0.0] * len(pairs)


# 全局单例（懒加载，首次使用时初始化）
_reranker_instance: Reranker | None = None


def get_reranker() -> Reranker:
    """获取 Reranker 单例（懒加载）。"""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = Reranker()
    return _reranker_instance
