"""Query 改写与扩展：使用 LLM 对用户查询进行预处理。

解决以下检索困难：
- 口语化表达（"半夜系统挂了" → "凌晨系统故障原因"）
- 指代消解（"那个7月份出问题的系统" → "Agent 系统 7月故障"）
- 过度指定（query 条件过多导致无法匹配）
- 词汇鸿沟（用户用词 ≠ 文档用词）

输出：rewritten_query + keywords + expanded_queries
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from src.db.config import settings

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_config.yaml"

_REWRITE_PROMPT = """你是一个知识库检索助手。用户将向你提出查询请求，你需要将其改写为更适合检索的形式。

用户查询：{query}

请按以下要求改写：

1. **rewritten_query**：将口语化、模糊或含指代的查询规范化为精确的检索查询。保留核心意图，使用正式词汇。如果查询本身已经清晰，则保持原样。

2. **keywords**：提取 3-5 个关键词，用于精确匹配。包括人名、项目名、技术术语、时间等关键实体。

3. **expanded_queries**：生成 1-2 个相关但不同角度的查询，覆盖同一问题的不同表述方式。

请严格返回 JSON 格式：
```json
{{
  "rewritten_query": "规范化后的查询",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "expanded_queries": ["扩展查询1"]
}}
```"""


@dataclass
class RewriteResult:
    """Query 改写结果。"""
    rewritten_query: str
    keywords: list[str] = field(default_factory=list)
    expanded_queries: list[str] = field(default_factory=list)


class QueryRewriter:
    """使用 LLM 进行 Query 改写。

    失败时降级：返回原始 query 作为 rewritten_query，空关键词和扩展。
    """

    def __init__(self) -> None:
        self._config = self._load_config()
        self._provider = self._config.get("provider", "openai")
        self._model = self._config.get("model", "qwen-turbo")
        self._base_url = self._config.get(
            "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self._api_key = self._config.get("api_key", "") or settings.llm_api_key

    @staticmethod
    def _load_config() -> dict:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
                return cfg.get("llm", {})
        return {}

    async def rewrite(self, query: str) -> RewriteResult:
        """改写查询。

        使用 LLM（qwen-turbo，低延迟）改写，超时 8s，失败则降级返回原始 query。
        """
        # 短查询（<=5字）可能太短，直接返回
        if len(query.strip()) <= 5:
            return RewriteResult(
                rewritten_query=query,
                keywords=[query.strip()],
                expanded_queries=[],
            )

        t0 = time.monotonic()
        try:
            result = await self._call_llm(query)
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"Query 改写完成: '{query[:40]}' → '{result.rewritten_query[:40]}' | "
                f"keywords={result.keywords} | expanded={len(result.expanded_queries)} | "
                f"耗时 {elapsed_ms:.0f}ms"
            )
            return result
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                f"Query 改写失败，降级使用原始 query: {type(e).__name__}: {e} | "
                f"耗时 {elapsed_ms:.0f}ms"
            )
            return RewriteResult(
                rewritten_query=query,
                keywords=[query],
                expanded_queries=[],
            )

    async def _call_llm(self, query: str) -> RewriteResult:
        """调用 LLM 改写 query。"""
        prompt = _REWRITE_PROMPT.format(query=query)

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": "你是一个专业的知识库检索助手。"},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        return self._parse_response(content, query)

    @staticmethod
    def _parse_response(raw: str, original_query: str) -> RewriteResult:
        """解析 LLM 返回的 JSON。"""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1])

        data = json.loads(text)

        rewritten = data.get("rewritten_query", original_query)
        keywords = data.get("keywords", [original_query])
        expanded = data.get("expanded_queries", [])

        # 确保 rewritten 非空
        if not rewritten or not rewritten.strip():
            rewritten = original_query

        return RewriteResult(
            rewritten_query=rewritten.strip(),
            keywords=[k.strip() for k in keywords if k and k.strip()],
            expanded_queries=[e.strip() for e in expanded if e and e.strip()],
        )


# 全局单例
query_rewriter = QueryRewriter()
