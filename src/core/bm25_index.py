"""BM25 关键词检索索引：基于 rank-bm25 + jieba 中文分词。

提供内存中的 BM25 索引，支持增量更新和关键词检索。
与向量检索互补，擅长精确匹配长尾实体和专有名词。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import jieba
from rank_bm25 import BM25Okapi

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, Document

logger = logging.getLogger(__name__)

# ── jieba 分词配置 ────────────────────────────────────────────
# 添加领域专有词汇，提升分词质量
_DOMAIN_WORDS = [
    "RAG", "Agent", "MCP", "SFT", "RLHF", "BM25", "HNSW", "pgvector",
    "ReAct", "Neo4j", "Locust", "MinHash", "LSH", "CrossEncoder",
    "Reranker", "Pipeline", "TaskExecutor", "FlowLLM",
    "Project Forge", "Project Hermes", "Project Atlas",
    "Qwen", "DeepSeek", "bge-reranker", "bge-m3",
    "Val Loss", "KL 散度", "Cohen's Kappa",
]
for w in _DOMAIN_WORDS:
    jieba.add_word(w)


def tokenize(text: str) -> list[str]:
    """中文 + 英文混合分词。

    策略：
    1. jieba 中文分词
    2. 保留英文单词和技术术语
    3. 过滤停用词和单字符
    """
    # jieba 分词
    tokens = jieba.lcut(text)
    # 过滤：去除空白、单字符（保留英文单字母如 R、K 等大写）
    result = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        # 保留长度>=2 的 token，或者全大写英文（如 RAG、SFT）
        if len(t) >= 2 or (t.isalpha() and t.isupper()):
            result.append(t.lower() if not t.isupper() else t)
    return result


@dataclass
class BM25Entry:
    """BM25 索引中的一条记录。"""
    chunk_id: str
    doc_id: str
    chunk_index: int
    chunk_text: str
    overview: str
    doc_uri: str


class BM25Index:
    """内存 BM25 索引，支持增量更新。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[BM25Entry] = []  # 与 BM25 corpus 对齐
        self._bm25: BM25Okapi | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def build_from_db(self, session: AsyncSession) -> None:
        """从 PostgreSQL 加载所有 indexed chunks 构建 BM25 索引。"""
        logger.info("BM25 索引开始构建...")

        stmt = (
            select(
                Chunk.id,
                Chunk.doc_id,
                Chunk.chunk_index,
                Chunk.chunk_text,
                Chunk.overview,
                Chunk.doc_uri,
            )
            .join(Document, Chunk.doc_id == Document.id)
            .where(Document.index_status == "indexed")
            .where(Document.file_status == "active")
        )
        result = await session.execute(stmt)
        rows = result.all()

        entries: list[BM25Entry] = []
        for row in rows:
            entries.append(BM25Entry(
                chunk_id=str(row.id),
                doc_id=str(row.doc_id),
                chunk_index=row.chunk_index,
                chunk_text=row.chunk_text,
                overview=row.overview,
                doc_uri=row.doc_uri,
            ))

        with self._lock:
            self._entries = entries
            corpus = [tokenize(e.chunk_text) for e in entries]
            if corpus:
                self._bm25 = BM25Okapi(corpus)
            else:
                self._bm25 = None
            self._ready = True

        logger.info(f"BM25 索引构建完成: {len(entries)} 条 chunks")

    def add_entry(self, entry: BM25Entry) -> None:
        """增量添加一条 chunk 到索引。"""
        with self._lock:
            self._entries.append(entry)
            self._rebuild_bm25()

    def remove_by_doc_id(self, doc_id: str) -> int:
        """删除指定文档的所有 chunks。"""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.doc_id != doc_id]
            removed = before - len(self._entries)
            if removed > 0:
                self._rebuild_bm25()
            return removed

    def search(self, query: str, top_k: int = 30) -> list[dict]:
        """BM25 关键词检索。

        Args:
            query: 查询文本（会经过 jieba 分词）
            top_k: 返回 Top-K 结果

        Returns:
            list of {chunk_id, doc_id, chunk_index, chunk_text, overview, doc_uri, score}
        """
        if not self._ready or self._bm25 is None or not self._entries:
            return []

        tokens = tokenize(query)
        if not tokens:
            return []

        with self._lock:
            scores = self._bm25.get_scores(tokens)
            # 获取 top_k 索引
            indexed_scores = list(enumerate(scores))
            indexed_scores.sort(key=lambda x: -x[1])

            results = []
            for idx, score in indexed_scores[:top_k]:
                if score <= 0:
                    break
                entry = self._entries[idx]
                results.append({
                    "chunk_id": entry.chunk_id,
                    "doc_id": entry.doc_id,
                    "chunk_index": entry.chunk_index,
                    "chunk_text": entry.chunk_text,
                    "overview": entry.overview,
                    "doc_uri": entry.doc_uri,
                    "score": float(score),
                })

        return results

    def _rebuild_bm25(self) -> None:
        """重建 BM25 索引（调用者需持有 lock）。"""
        corpus = [tokenize(e.chunk_text) for e in self._entries]
        if corpus:
            self._bm25 = BM25Okapi(corpus)
        else:
            self._bm25 = None


# 全局单例
bm25_index = BM25Index()
