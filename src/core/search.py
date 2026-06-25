"""三层漏斗检索：向量粗筛 → Overview 守门 → 图谱增强。"""

from __future__ import annotations

from dataclasses import dataclass, field

from pgvector.sqlalchemy import Vector
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.embedder import embedder

# 默认参数
DEFAULT_TOP_K = 20
GATEKEEPER_THRESHOLD = 0.7


@dataclass
class SearchSource:
    doc_id: str
    title: str
    chunk_text: str
    score: float


@dataclass
class SearchResult:
    answer: str
    sources: list[SearchSource] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)


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

    # pgvector cosine distance: 1 - cosine_similarity
    # 所以 ORDER BY embedding <=> query_embedding 是从小到大（距离）
    stmt = (
        select(
            Chunk.id.label("chunk_id"),
            Chunk.chunk_text,
            Chunk.overview,
            Chunk.doc_uri,
            Chunk.doc_id,
            # cosine distance → similarity
            (1 - Chunk.embedding.cosine_distance(query_embedding)).label("score"),
        )
        .order_by(Chunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "chunk_id": str(row.chunk_id),
            "chunk_text": row.chunk_text,
            "score": float(row.score),
            "overview": row.overview,
            "doc_uri": row.doc_uri,
            "doc_id": str(row.doc_id),
        }
        for row in rows
    ]


async def gatekeeper_filter(
    query: str,
    candidates: list[dict],
    threshold: float = GATEKEEPER_THRESHOLD,
) -> list[dict]:
    """第二层：Overview 守门（embedding cosine 过滤）。

    对每个候选 chunk 的 overview 进行 embedding，
    与 query embedding 做 cosine similarity，低于阈值则过滤。

    overview embedding 按文档缓存（同一文档的 overview 相同）。
    """
    if not candidates:
        return []

    query_embedding = await embedder.embed_text(query)

    # 缓存 overview embeddings（同文档 overview 相同）
    overview_cache: dict[str, list[float]] = {}
    survivors: list[dict] = []

    for c in candidates:
        overview = c.get("overview", "")
        if not overview:
            # 无 overview 的直接通过
            survivors.append(c)
            continue

        # 缓存命中
        if overview in overview_cache:
            ov_embedding = overview_cache[overview]
        else:
            ov_embedding = await embedder.embed_text(overview)
            overview_cache[overview] = ov_embedding

        # cosine similarity
        similarity = _cosine_similarity(query_embedding, ov_embedding)
        if similarity >= threshold:
            survivors.append(c)

    return survivors


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def graph_enrich(
    neo4j: Neo4jClient,
    survivors: list[dict],
    hops: int = 2,
) -> list[GraphQueryResult]:
    """第三层（图谱部分）：从存活 chunks 关联的文档中查询 Neo4j 实体。

    Returns:
        相关实体列表（去重）
    """
    doc_ids = {c["doc_id"] for c in survivors}
    all_entities: dict[str, GraphQueryResult] = {}

    for doc_id in doc_ids:
        entities = await neo4j.get_document_entities(doc_id)
        for entity in entities:
            if entity.name not in all_entities:
                # 查询实体的邻居关系
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
    threshold: float = GATEKEEPER_THRESHOLD,
) -> SearchResult:
    """完整三层漏斗检索。

    1. 向量粗筛 → Top-K
    2. Overview 守门 → 过滤
    3. 图谱增强 → 相关实体

    注意：第三层的 LLM 答案合成暂不实现（需 LLM 配置完成后接入），
    当前返回 sources + related_entities。
    """
    # 第一层
    candidates = await vector_search(session, query, top_k)

    # 第二层
    survivors = await gatekeeper_filter(query, candidates, threshold)

    # 第三层（图谱）
    related_entities = await graph_enrich(neo4j, survivors)

    # 构造结果
    sources = [
        SearchSource(
            doc_id=c["doc_id"],
            title=c.get("doc_uri", "").split(":", 1)[-1] if ":" in c.get("doc_uri", "") else "",
            chunk_text=c["chunk_text"],
            score=c["score"],
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

    # 暂时不合成答案（需 LLM），直接返回来源
    answer = f"找到 {len(sources)} 个相关内容片段，涉及 {len(entity_dicts)} 个实体。"

    return SearchResult(
        answer=answer,
        sources=sources,
        related_entities=entity_dicts,
    )
