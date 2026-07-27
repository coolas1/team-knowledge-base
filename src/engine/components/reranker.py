"""Reranker 服务：使用 bge-reranker-v2-m3 交叉编码器对 (query, text) 对打分。"""

from __future__ import annotations

import logging
import os

# 优先使用本地缓存，避免每次加载都尝试联网检查
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

# 默认模型配置
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class Reranker:
    """基于 CrossEncoder 的重排序模型。

    对 (query, text) 文本对进行相关性打分，
    用于检索守门层替换原有的 cosine similarity。
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        logger.info("加载 Reranker 模型: %s ...", model_name)
        self.model = CrossEncoder(model_name)
        logger.info("Reranker 模型加载完成 ✓")

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """对 (query, text) 对打分，返回相关性分数列表。

        Args:
            query: 搜索查询
            texts: 待打分的文本列表

        Returns:
            与 texts 等长的分数列表
        """
        if not texts:
            return []
        pairs = [(query, text) for text in texts]
        scores = self.model.predict(pairs)
        return [float(s) for s in scores]


# 全局单例（懒加载，首次使用时初始化）
_reranker_instance: Reranker | None = None


def get_reranker() -> Reranker:
    """获取 Reranker 单例（懒加载）。"""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = Reranker()
    return _reranker_instance
