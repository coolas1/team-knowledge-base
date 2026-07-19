"""混合检索引擎：多路召回 + RRF 融合 + Reranker 守门 + 图谱增强。

检索流程：
  Query → QueryRewriter → [rewritten + keywords + expanded]
    ├─ L1a: 向量检索 (rewritten_query, top_k=30)
    ├─ L1b: 向量检索 (expanded_queries, top_k=20 each)
    ├─ L1c: BM25 检索 (keywords, top_k=30)
    └─ RRF 融合 → merged_candidates
         │
         L2: Reranker (top_n=15)
         │
         L3: 图谱增强 (实体回灌 + 关联文档发现)
         │
         输出: SearchResult
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.bm25_index import bm25_index
from src.core.query_rewriter import query_rewriter
from src.core.reranker import get_reranker
from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, GraphQueryResult
from src.pipeline.embedder import embedder

logger = logging.getLogger(__name__)

# ── 检索参数 ──────────────────────────────────────────────────
VECTOR_TOP_K = 30           # 主查询向量检索 Top-K
EXPANDED_TOP_K = 20         # 扩展查询向量检索 Top-K
BM25_TOP_K = 30             # BM25 检索 Top-K
RERANKER_TOP_N = 15         # Reranker 守门后保留数
RERANKER_THRESHOLD = 0.01   # Reranker 最低分数阈值
RRF_K = 60                  # RRF 融合常数


@dataclass
class SearchChunk:
    doc_id: str
    title: str
    chunk_text: str
    reranker_score: float
    vector_score: float
    index_status: str = "indexed"


@dataclass
class SearchResult:
    chunks: list[SearchChunk] = field(default_factory=list)
    related_entities: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)
    debug: dict = field(default_factory=dict)  # 检索全链路调试信息


# ── 向量检索 ──────────────────────────────────────────────────

async def vector_search(
    session: AsyncSession,
    query: str,
    top_k: int = VECTOR_TOP_K,
) -> list[dict]:
    """向量检索：query → embed → pgvector 余弦相似度 → Top-K chunks。"""
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
        .where(Document.file_status == "active")
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


# ── RRF 融合 ──────────────────────────────────────────────────

def rrf_fuse(
    ranked_lists: list[list[dict]],
    k: int = RRF_K,
) -> list[dict]:
    """Reciprocal Rank Fusion：多路召回结果融合。

    公式：RRF_score(d) = sum(1 / (k + rank_i(d)))
    同一 chunk 在多个列表中出现时分数累加。

    Args:
        ranked_lists: 多个按相关性排序的候选列表
        k: RRF 常数（默认 60）

    Returns:
        按 RRF 分数降序排列的去重候选列表
    """
    rrf_scores: dict[str, float] = {}
    chunk_map: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, chunk in enumerate(ranked_list):
            cid = chunk["chunk_id"]
            score = 1.0 / (k + rank + 1)  # rank 从 0 开始
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + score
            if cid not in chunk_map:
                chunk_map[cid] = chunk

    # 按 RRF 分数降序排列
    sorted_ids = sorted(rrf_scores.keys(), key=lambda x: -rrf_scores[x])

    # 将 RRF 分数写入 chunk 数据
    results = []
    for cid in sorted_ids:
        chunk = chunk_map[cid].copy()
        chunk["rrf_score"] = rrf_scores[cid]
        results.append(chunk)

    return results


# ── Reranker 守门 ─────────────────────────────────────────────

async def reranker_filter(
    query: str,
    candidates: list[dict],
    threshold: float = RERANKER_THRESHOLD,
    top_n: int = RERANKER_TOP_N,
) -> list[dict]:
    """Reranker 守门：对 RRF 融合后的候选重新打分排序。

    overview 为空时 fallback 到 chunk_text。
    """
    if not candidates:
        return []

    # 准备打分文本
    texts = []
    for c in candidates:
        ov = c.get("overview", "")
        if ov and not ov.startswith("[待 LLM") and not ov.startswith("["):
            texts.append(ov)
        else:
            texts.append(c.get("chunk_text", ""))

    scores = await get_reranker().areank(query, texts)

    # 全零分 → Reranker 降级，使用 RRF 分数
    if all(s == 0.0 for s in scores):
        logger.info("Reranker 返回全零分，降级使用 RRF 分数排序")
        scored = [(c, c.get("rrf_score", 0.0)) for c in candidates]
        scored.sort(key=lambda x: -x[1])
        survivors = []
        for c, s in scored:
            c["reranker_score"] = s
            survivors.append(c)
            if len(survivors) >= top_n:
                break
        return survivors

    # 按 reranker 分数降序
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: -x[1])

    survivors = []
    for c, s in scored:
        if s >= threshold:
            c["reranker_score"] = s
            survivors.append(c)
        if len(survivors) >= top_n:
            break

    return survivors


# ── 图谱增强 ──────────────────────────────────────────────────

async def graph_enrich(
    neo4j: Neo4jClient,
    survivors: list[dict],
    session: AsyncSession,
) -> tuple[list[GraphQueryResult], list[dict]]:
    """图谱增强：从存活 chunks 提取实体，发现关联实体和 chunks。

    Returns:
        (related_entities, graph_discovered_chunks)
    """
    all_entities: dict[str, GraphQueryResult] = {}
    existing_chunk_ids = {c["chunk_id"] for c in survivors}
    graph_chunks: list[dict] = []

    for c in survivors:
        doc_id = c["doc_id"]
        chunk_index = c.get("chunk_index", 0)

        try:
            entities = await neo4j.find_entities_by_source(doc_id, chunk_index)
        except Exception:
            continue

        for entity in entities:
            if entity.name not in all_entities:
                details = await neo4j.get_entity_details(entity.name)
                if details:
                    all_entities[entity.name] = details
                else:
                    all_entities[entity.name] = entity

    # 从关联实体中发现新的 chunks（实体回灌）
    existing_doc_ids = {c["doc_id"] for c in survivors}
    discovered_chunk_keys: set[tuple[str, int]] = set()

    for entity_result in all_entities.values():
        # 查找关联实体
        for rel in entity_result.relations:
            other_name = rel.get("other_name", "")
            if not other_name:
                continue
            try:
                other_details = await neo4j.get_entity_details(other_name)
                if not other_details:
                    continue
                # 从关联实体的 sources 中发现新文档的 chunks
                sources_raw = other_details.properties.get("sources", "[]")
                import json
                sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
                for src in sources:
                    src_doc_id = src.get("doc_id", "")
                    src_chunk_idx = src.get("chunk_index", 0)
                    key = (src_doc_id, src_chunk_idx)
                    if (
                        src_doc_id
                        and src_doc_id not in existing_doc_ids
                        and key not in discovered_chunk_keys
                    ):
                        discovered_chunk_keys.add(key)
            except Exception:
                continue

    # 批量查询图谱发现的 chunks
    if discovered_chunk_keys:
        try:
            graph_chunks = await _fetch_chunks_by_keys(session, discovered_chunk_keys)
        except Exception as e:
            logger.warning(f"图谱发现 chunks 查询失败: {type(e).__name__}: {e}")

    return list(all_entities.values()), graph_chunks


async def _fetch_chunks_by_keys(
    session: AsyncSession,
    chunk_keys: set[tuple[str, int]],
    limit: int = 20,
) -> list[dict]:
    """根据 (doc_id, chunk_index) 批量查询 chunks。"""
    import uuid as _uuid

    results: list[dict] = []
    # 按 doc_id 分组查询
    doc_chunk_map: dict[str, list[int]] = {}
    for doc_id, chunk_idx in chunk_keys:
        doc_chunk_map.setdefault(doc_id, []).append(chunk_idx)

    for doc_id, indices in list(doc_chunk_map.items())[:limit]:
        try:
            doc_uuid = _uuid.UUID(doc_id)
        except ValueError:
            continue

        stmt = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.chunk_index,
                Chunk.chunk_text,
                Chunk.overview,
                Chunk.doc_uri,
                Chunk.doc_id,
                Document.index_status,
            )
            .join(Document, Chunk.doc_id == Document.id)
            .where(Chunk.doc_id == doc_uuid, Chunk.chunk_index.in_(indices))
        )
        result = await session.execute(stmt)
        for row in result.all():
            results.append({
                "chunk_id": str(row.chunk_id),
                "chunk_index": row.chunk_index,
                "chunk_text": row.chunk_text,
                "score": 0.5,  # 图谱发现，给一个中间分数
                "overview": row.overview,
                "doc_uri": row.doc_uri,
                "doc_id": str(row.doc_id),
                "index_status": row.index_status or "indexed",
                "reranker_score": 0.5,
            })

    return results


# ── 关联文档补充检索 ──────────────────────────────────────────

async def related_docs_search(
    neo4j: Neo4jClient,
    session: AsyncSession,
    existing_doc_ids: set[str],
    query_embedding: list[float],
    top_k: int = 5,
) -> list[dict]:
    """对关联文档做轻量级向量检索，补充未在结果中的关联 chunks。"""
    try:
        related_docs = await neo4j.get_related_docs(list(existing_doc_ids))
    except Exception:
        return []

    if not related_docs:
        return []

    # 找出不在现有结果中的关联文档
    new_doc_ids = {
        rd["doc_id"] for rd in related_docs if rd["doc_id"] not in existing_doc_ids
    }
    if not new_doc_ids:
        return []

    # 对这些文档做轻量级向量检索
    import uuid as _uuid

    results: list[dict] = []
    for doc_id in list(new_doc_ids)[:3]:  # 最多补充 3 个关联文档
        try:
            doc_uuid = _uuid.UUID(doc_id)
        except ValueError:
            continue

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
            .where(Chunk.doc_id == doc_uuid)
            .order_by(Chunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        try:
            result = await session.execute(stmt)
            for row in result.all():
                results.append({
                    "chunk_id": str(row.chunk_id),
                    "chunk_index": row.chunk_index,
                    "chunk_text": row.chunk_text,
                    "score": float(row.score),
                    "overview": row.overview,
                    "doc_uri": row.doc_uri,
                    "doc_id": str(row.doc_id),
                    "index_status": row.index_status or "indexed",
                    "reranker_score": float(row.score),
                })
        except Exception:
            continue

    return results


# ── 2 轮迭代多跳检索 ─────────────────────────────────────

async def iterative_expand(
    neo4j: Neo4jClient,
    session: AsyncSession,
    first_round: list[dict],
    base_query: str,
    top_chunks: int = 4,
    expand_top_k: int = 8,
) -> list[dict]:
    """2 轮迭代多跳检索。

    从首轮 top chunks 中提取实体作为“锚点”，再获取这些实体的
    1-hop 邻居，用实体锚点构造第二轮向量检索，扩展候选池。
    用于改善多约束/多跳题（如“同时满足 A∩B∩C 的实体”）。
    """
    if not first_round:
        return []

    # 1. 从首轮 top chunks 提取实体名
    entity_names: set[str] = set()
    for c in first_round[:top_chunks]:
        try:
            ents = await neo4j.find_entities_by_source(c["doc_id"], c.get("chunk_index", 0))
            for e in ents:
                if e.name:
                    entity_names.add(e.name)
        except Exception:
            continue

    if not entity_names:
        return []

    # 2. 获取实体的 1-hop 邻居，扩展锚点集
    anchor_names: set[str] = set(entity_names)
    for name in list(entity_names)[:8]:
        try:
            details = await neo4j.get_entity_details(name)
            if details:
                for rel in details.relations:
                    other = rel.get("other_name", "")
                    if other:
                        anchor_names.add(other)
        except Exception:
            continue

    # 3. 用实体锚点构造第二轮查询，扩展候选
    existing_ids = {c["chunk_id"] for c in first_round}
    second_round: list[dict] = []
    anchor_list = list(anchor_names)[:10]

    # 查询 A：原始查询 + 实体锚点拼接（找实体共现的 chunks）
    entity_query = f"{base_query} " + " ".join(anchor_list[:4])
    try:
        hits = await vector_search(session, entity_query, expand_top_k)
        for h in hits:
            if h["chunk_id"] not in existing_ids:
                second_round.append(h)
                existing_ids.add(h["chunk_id"])
    except Exception:
        pass

    # 查询 B：逐个关键实体 + 原始查询（捕捉单实体相关 chunks）
    for name in anchor_list[:2]:
        try:
            hits = await vector_search(session, f"{name} {base_query}", expand_top_k // 2)
            for h in hits:
                if h["chunk_id"] not in existing_ids:
                    second_round.append(h)
                    existing_ids.add(h["chunk_id"])
        except Exception:
            continue

    # 限制扩展总量，避免淹没首轮高质量结果（如多模态图片 chunk）
    return second_round[:expand_top_k]


# ── 完整检索流程 ──────────────────────────────────────────────

async def full_search(
    session: AsyncSession,
    neo4j: Neo4jClient,
    query: str,
) -> SearchResult:
    """完整检索流程：Query改写 → 多路召回 → RRF融合 → Reranker → 图谱增强。"""
    _t0 = time.monotonic()

    # ── Step 0: Query 改写 ──────────────────────────────────────
    rewrite_t0 = time.monotonic()
    try:
        rewrite_result = await query_rewriter.rewrite(query)
    except Exception as e:
        logger.warning(f"Query 改写异常，降级使用原始 query: {e}")
        from src.core.query_rewriter import RewriteResult
        rewrite_result = RewriteResult(
            rewritten_query=query, keywords=[query], expanded_queries=[]
        )
    rewrite_ms = (time.monotonic() - rewrite_t0) * 1000

    rewritten = rewrite_result.rewritten_query
    keywords = rewrite_result.keywords
    expanded = rewrite_result.expanded_queries

    logger.info(
        f"搜索 Query 改写: '{query[:50]}' → '{rewritten[:50]}' | "
        f"keywords={keywords[:3]} | expanded={len(expanded)} | "
        f"耗时 {rewrite_ms:.0f}ms"
    )

    # ── Step 1: 多路召回（并行） ────────────────────────────────
    recall_t0 = time.monotonic()

    # L1a: 主查询向量检索
    # L1b: 扩展查询向量检索
    # L1c: BM25 关键词检索
    vector_main_task = vector_search(session, rewritten, VECTOR_TOP_K)
    bm25_results = bm25_index.search(" ".join(keywords) if keywords else query, BM25_TOP_K)

    # 并行执行向量检索（主查询 + 扩展查询）
    vector_tasks = [vector_main_task]
    for eq in expanded[:2]:
        vector_tasks.append(vector_search(session, eq, EXPANDED_TOP_K))

    try:
        vector_results = await asyncio.gather(*vector_tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"多路向量检索失败: {e}", exc_info=True)
        vector_results = [[]]

    # 处理结果
    main_candidates = vector_results[0] if not isinstance(vector_results[0], Exception) else []
    expanded_candidates = []
    for vr in vector_results[1:]:
        if not isinstance(vr, Exception):
            expanded_candidates.extend(vr)

    recall_ms = (time.monotonic() - recall_t0) * 1000
    logger.info(
        f"搜索 L1 多路召回: 向量主={len(main_candidates)} | "
        f"向量扩展={len(expanded_candidates)} | BM25={len(bm25_results)} | "
        f"耗时 {recall_ms:.0f}ms"
    )

    # ── Step 1.5: RRF 融合 ─────────────────────────────────────
    ranked_lists = [main_candidates]
    if expanded_candidates:
        ranked_lists.append(expanded_candidates)
    if bm25_results:
        ranked_lists.append(bm25_results)

    merged = rrf_fuse(ranked_lists)
    logger.info(f"搜索 RRF 融合: {len(merged)} 候选（去重后）")

    # 如果没有候选，直接返回空
    if not merged:
        return SearchResult()

    # ── Step 1.6: 2 轮迭代多跳扩展 ───────────────────────
    expand_t0 = time.monotonic()
    try:
        second_round = await iterative_expand(neo4j, session, merged, rewritten)
        if second_round:
            # 第二轮候选降权加入候选池，由 Reranker 重新打分决定去留
            for c in second_round:
                c["rrf_score"] = c.get("score", 0.0) * 0.3
            merged = merged + second_round
            logger.info(f"搜索迭代多跳扩展: +{len(second_round)} 候选 → 共 {len(merged)}")
    except Exception as e:
        logger.warning(f"搜索迭代多跳扩展失败，跳过: {type(e).__name__}: {e}")
    expand_ms = (time.monotonic() - expand_t0) * 1000

    # ── Step 2: Reranker 守门 ──────────────────────────────────
    reranker_t0 = time.monotonic()
    survivors: list[dict] = []
    try:
        survivors = await reranker_filter(rewritten, merged, RERANKER_THRESHOLD, RERANKER_TOP_N)
    except Exception as e:
        logger.error(f"搜索 L2 Reranker 失败: {type(e).__name__}: {e}", exc_info=True)
        # 降级：取 RRF 融合前 N 个
        survivors = merged[:RERANKER_TOP_N]
        for c in survivors:
            c.setdefault("reranker_score", c.get("rrf_score", 0.0))
    reranker_ms = (time.monotonic() - reranker_t0) * 1000
    logger.info(
        f"搜索 L2 Reranker: {len(merged)}→{len(survivors)} 存活 | "
        f"耗时 {reranker_ms:.0f}ms"
    )

    # ── Step 3: 图谱增强 ───────────────────────────────────────
    graph_t0 = time.monotonic()
    related_entities: list[GraphQueryResult] = []
    graph_chunks: list[dict] = []
    try:
        related_entities, graph_chunks = await graph_enrich(neo4j, survivors, session)
    except Exception as e:
        logger.warning(f"搜索 L3 图谱增强失败: {type(e).__name__}: {e}")
    graph_ms = (time.monotonic() - graph_t0) * 1000
    logger.info(
        f"搜索 L3 图谱增强: {len(related_entities)} 实体, "
        f"{len(graph_chunks)} 图谱chunks | 耗时 {graph_ms:.0f}ms"
    )

    # ── Step 3.5: 关联文档补充检索 ─────────────────────────────
    existing_doc_ids = {c["doc_id"] for c in survivors}
    related_doc_chunks: list[dict] = []
    try:
        # 用主查询的 embedding 做关联文档检索
        query_embedding = await embedder.embed_text(rewritten)
        related_doc_chunks = await related_docs_search(
            neo4j, session, existing_doc_ids, query_embedding, top_k=5
        )
    except Exception as e:
        logger.warning(f"搜索关联文档补充失败: {type(e).__name__}: {e}")

    # ── 合并所有 chunks ────────────────────────────────────────
    all_survivors: list[dict] = list(survivors)
    # 图谱/关联文档发现的 chunks 不应固定 0.5 堆在末尾，
    # 而应过 Reranker 获得真实相关性分数，让真正相关的（如多模态图片）能按分数排上来。
    supplementary = graph_chunks + related_doc_chunks
    if supplementary:
        survivor_ids = {c["chunk_id"] for c in survivors}
        supp_seen: set[str] = set()
        supp_unique: list[dict] = []
        for c in supplementary:
            cid = c["chunk_id"]
            if cid not in survivor_ids and cid not in supp_seen:
                supp_seen.add(cid)
                supp_unique.append(c)
        if supp_unique:
            try:
                scored_supp = await reranker_filter(
                    rewritten, supp_unique, RERANKER_THRESHOLD, len(supp_unique)
                )
            except Exception as e:
                logger.warning(f"补充 chunks Reranker 打分失败，使用原始分: {e}")
                scored_supp = supp_unique
            all_survivors = survivors + scored_supp

    # 去重（按 chunk_id）并按 reranker 分数降序排列
    seen_ids: set[str] = set()
    deduped: list[dict] = []
    for c in all_survivors:
        cid = c["chunk_id"]
        if cid not in seen_ids:
            seen_ids.add(cid)
            deduped.append(c)
    deduped.sort(key=lambda x: -float(x.get("reranker_score", x.get("rrf_score", 0.0))))

    # 构造 SearchChunk
    chunks = [
        SearchChunk(
            doc_id=c["doc_id"],
            title=(
                c.get("doc_uri", "").split(":", 1)[-1]
                if ":" in c.get("doc_uri", "")
                else ""
            ),
            chunk_text=c["chunk_text"],
            reranker_score=c.get("reranker_score", c.get("rrf_score", 0.0)),
            vector_score=c.get("score", 0.0),
            index_status=c.get("index_status", "indexed"),
        )
        for c in deduped
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
    related_docs: list[dict] = []
    try:
        doc_ids = list(existing_doc_ids)
        related_docs = await neo4j.get_related_docs(doc_ids)
    except Exception as e:
        logger.warning(f"搜索关联文档查询失败: {type(e).__name__}: {e}")

    total_ms = (time.monotonic() - _t0) * 1000
    logger.info(
        f"搜索全链路: query='{query[:50]}' | "
        f"改写 {rewrite_ms:.0f}ms → "
        f"召回 {recall_ms:.0f}ms({len(main_candidates)}+{len(expanded_candidates)}+{len(bm25_results)}) → "
        f"RRF {len(merged)} → "
        f"Reranker {reranker_ms:.0f}ms({len(survivors)}存活) → "
        f"图谱 {graph_ms:.0f}ms({len(related_entities)}实体+{len(graph_chunks)}chunks) | "
        f"总耗时 {total_ms:.0f}ms | 最终 {len(chunks)} chunks"
    )

    # 检索全链路调试信息（供前端可视化排查召回问题）
    debug = {
        "rewrite": {
            "original": query,
            "rewritten": rewritten,
            "keywords": keywords,
            "expanded_queries": expanded,
            "elapsed_ms": round(rewrite_ms, 1),
        },
        "recall": {
            "vector_main": len(main_candidates),
            "vector_expanded": len(expanded_candidates),
            "bm25": len(bm25_results),
            "rrf_merged": len(merged),
            "elapsed_ms": round(recall_ms, 1),
        },
        "iterative_expand": {
            "added": len(merged) - (len(ranked_lists[0]) if ranked_lists else 0),
            "elapsed_ms": round(expand_ms, 1),
        },
        "reranker": {
            "input": len(merged),
            "survivors": len(survivors),
            "threshold": RERANKER_THRESHOLD,
            "top_n": RERANKER_TOP_N,
            "elapsed_ms": round(reranker_ms, 1),
        },
        "graph": {
            "entities": len(related_entities),
            "graph_chunks": len(graph_chunks),
            "related_doc_chunks": len(related_doc_chunks),
            "elapsed_ms": round(graph_ms, 1),
        },
        "total_ms": round(total_ms, 1),
        "final_chunks": len(chunks),
    }

    return SearchResult(
        chunks=chunks,
        related_entities=entity_dicts,
        related_docs=related_docs,
        debug=debug,
    )
