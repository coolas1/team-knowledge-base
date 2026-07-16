"""两层漏斗检索：向量粗筛 → Reranker 守门 + 图谱增强。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.reranker import get_reranker
from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.embedder import embedder

logger = logging.getLogger(__name__)

# 默认参数
DEFAULT_TOP_K = 20
RERANKER_THRESHOLD = 0.01
RERANKER_TOP_N = 10


@dataclass
class SearchChunk:
    doc_id: str
    title: str
    chunk_text: str
    reranker_score: float
    vector_score: float
    index_status: str = "indexed"


@dataclass
class RelatedDoc:
    doc_id: str
    title: str
    relation_type: str
    reason: str


@dataclass
class SearchResult:
    chunks: list[SearchChunk] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)


async def vector_search(
    session: AsyncSession,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """第一层：向量粗筛。

    query → embed → pgvector 余弦相似度 → Top-K chunks

    Returns:
        list of {chunk_text, score, overview, doc_uri, doc_id, chunk_id}
    """
    query_embedding = await embedder.embed_text(query)

    stmt = (
        select(
            Chunk.id.label("chunk_id"),
            Chunk.chunk_index,
            Chunk.chunk_text,
            Chunk.overview,
            Chunk.doc_uri,
            Chunk.doc_id,
            (1 - Chunk.embedding.cosine_distance(query_embedding)).label("score"),
            Document.index_status,
        )
        .join(Document, Chunk.doc_id == Document.id)
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "chunk_id": str(row.chunk_id),
            "chunk_index": row.chunk_index,
            "chunk_text": row.chunk_text,
            "score": float(row.score),
            "overview": row.overview,
            "doc_uri": row.doc_uri,
            "doc_id": str(row.doc_id),
            "index_status": row.index_status or "indexed",
        }
        for row in rows
    ]


def reranker_filter(
    query: str,
    candidates: list[dict],
    threshold: float = RERANKER_THRESHOLD,
    top_n: int = RERANKER_TOP_N,
) -> list[dict]:
    """第二层：Reranker 守门。

    使用 CrossEncoder 对 (query, overview) 对打分，
    按分数排序 + 阈值过滤 + Top-N。

    overview 为空时 fallback 到 chunk_text。
    """
    if not candidates:
        return []

    # 准备打分文本：优先真实 overview，占位/空则 fallback 到 chunk_text
    texts = []
    for c in candidates:
        ov = c.get("overview", "")
        if ov and not ov.startswith("[待 LLM"):
            texts.append(ov)
        else:
            texts.append(c.get("chunk_text", ""))

    scores = get_reranker().rerank(query, texts)

    # 按分数降序排序
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: -x[1])

    survivors: list[dict] = []
    for c, s in scored:
        if s >= threshold:
            c["reranker_score"] = s
            survivors.append(c)
        if len(survivors) >= top_n:
            break

    return survivors


async def graph_enrich(
    neo4j: Neo4jClient,
    survivors: list[dict],
    hops: int = 2,
) -> list[GraphQueryResult]:
    """从存活 chunks 的 (doc_id, chunk_index) 查找关联实体。

    通过 Entity.sources 属性反查哪些实体来自这些 chunk，
    然后遍历实体关系。
    """
    all_entities: dict[str, GraphQueryResult] = {}

    for c in survivors:
        doc_id = c["doc_id"]
        chunk_index = c.get("chunk_index", 0)

        entities = await neo4j.find_entities_by_source(doc_id, chunk_index)
        for entity in entities:
            if entity.name not in all_entities:
                details = await neo4j.get_entity_details(entity.name)
                if details:
                    all_entities[entity.name] = details
                else:
                    all_entities[entity.name] = entity

    return list(all_entities.values())


async def full_search(
    session: AsyncSession,
    neo4j: Neo4jClient,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = RERANKER_THRESHOLD,
    top_n: int = RERANKER_TOP_N,
) -> SearchResult:
    """完整检索流程：向量粗筛 → Reranker 守门 → 图谱增强 + 关联文档。

    Returns:
        SearchResult(chunks, related_entities, related_docs) — 无 answer，由 Agent 合成。
    """
    # 第一层：向量粗筛
    _t0 = time.monotonic()
    candidates = await vector_search(session, query, top_k)
    vector_ms = (time.monotonic() - _t0) * 1000
    logger.info(
        f"搜索 L1 向量粗筛: query='{query[:60]}' | "
        f"返回 {len(candidates)} 候选 | 耗时 {vector_ms:.0f}ms"
    )

    # 第二层：Reranker 守门
    _t1 = time.monotonic()
    survivors = reranker_filter(query, candidates, threshold, top_n)
    reranker_ms = (time.monotonic() - _t1) * 1000
    logger.info(
        f"搜索 L2 Reranker: {len(candidates)}→{len(survivors)} 存活 | "
        f"耗时 {reranker_ms:.0f}ms"
    )

    # 第三层：图谱增强
    _t2 = time.monotonic()
    related_entities = await graph_enrich(neo4j, survivors)
    graph_ms = (time.monotonic() - _t2) * 1000
    logger.info(
        f"搜索 L3 图谱增强: {len(related_entities)} 关联实体 | "
        f"耗时 {graph_ms:.0f}ms"
    )

    # 构造结果
    chunks = [
        SearchChunk(
            doc_id=c["doc_id"],
            title=(
                c.get("doc_uri", "").split(":", 1)[-1]
                if ":" in c.get("doc_uri", "")
                else ""
            ),
            chunk_text=c["chunk_text"],
            reranker_score=c["reranker_score"],
            vector_score=c["score"],
            index_status=c.get("index_status", "indexed"),
        )
        for c in survivors
    ]

    entity_dicts = [
        {
            "name": e.name,
            "type": e.entity_type,
            "relations": e.relations,
        }
        for e in related_entities
    ]

    # 查询关联文档
    doc_ids = list({c["doc_id"] for c in survivors})
    related_docs = await neo4j.get_related_docs(doc_ids)

    total_ms = (time.monotonic() - _t0) * 1000
    logger.info(
        f"搜索全链路: query='{query[:60]}' | "
        f"向量 {vector_ms:.0f}ms({len(candidates)}候選) → "
        f"Reranker {reranker_ms:.0f}ms({len(survivors)}存活) → "
        f"图谱 {graph_ms:.0f}ms({len(related_entities)}实体) | "
        f"总耗时 {total_ms:.0f}ms"
    )

    return SearchResult(
        chunks=chunks,
        related_entities=entity_dicts,
        related_docs=related_docs,
    )
