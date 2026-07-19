"""检索管线逐段诊断工具。

三维度诊断：
  1. 漏斗追踪：目标文档在每段的排名/分数/存活状态
  2. 耗时分布：每段绝对耗时和占比
  3. 内容质量：分块完整性、overview 忠实度、信息密度

用法：
  # 单题诊断
  python diag_pipeline.py --query "your query" --gold "2023-11-15.md"

  # 从 benchmark 加载
  python diag_pipeline.py --bench 13

  # 批量跑所有 benchmark
  python diag_pipeline.py --bench all --summary
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 清除代理环境变量
for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(k, None)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))

BENCH_DIR = Path(__file__).parent.parent / "rag-agent-bench" / "eval" / "qa"


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class StageMetrics:
    """单段诊断指标。"""
    name: str
    elapsed_ms: float = 0.0
    # 目标追踪
    target_hit: bool = False
    target_rank: int = -1  # -1 = not found
    target_score: float = 0.0
    # 路径级追踪（仅召回段）
    path_hits: dict = field(default_factory=dict)  # path_name -> (rank, score)
    # 统计
    total_candidates: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class DiagReport:
    """完整诊断报告。"""
    query: str
    gold_filenames: list[str]
    stages: list[StageMetrics] = field(default_factory=list)
    total_ms: float = 0.0
    final_rank: int = -1
    verdict: str = ""  # ✓ top-5 / △ top-15 / ✗ lost
    # 内容质量
    content_quality: dict = field(default_factory=dict)


# ── 工具函数 ──────────────────────────────────────────────────

def _is_target(chunk: dict, gold_filenames: list[str]) -> bool:
    """判断 chunk 是否属于目标文档。"""
    doc_uri = chunk.get("doc_uri", "")
    # doc_uri 格式: "uuid:filename"
    filename = doc_uri.split(":", 1)[-1] if ":" in doc_uri else doc_uri
    return any(g in filename for g in gold_filenames)


def _find_target_rank(chunks: list[dict], gold_filenames: list[str]) -> tuple[int, float]:
    """找到目标在列表中的排名和分数。返回 (rank, score)，未找到返回 (-1, 0)。"""
    for i, c in enumerate(chunks):
        if _is_target(c, gold_filenames):
            score = c.get("reranker_score", c.get("rrf_score", c.get("score", 0.0)))
            return i + 1, float(score)
    return -1, 0.0


def _load_bench_question(qid: int) -> tuple[str, list[str]]:
    """从 benchmark 加载问题和 gold 文件名。"""
    q_file = BENCH_DIR / "questions" / f"{qid:02d}-q.md"
    a_file = BENCH_DIR / "answers" / f"{qid:02d}-a.md"

    if not q_file.exists():
        raise FileNotFoundError(f"Question file not found: {q_file}")

    # 提取问题文本（去掉 frontmatter 和标题）
    q_text = q_file.read_text(encoding="utf-8")
    q_lines = q_text.split("\n")
    in_frontmatter = False
    question_lines = []
    for line in q_lines:
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if line.startswith("# "):
            continue
        question_lines.append(line)
    query = "\n".join(question_lines).strip()

    # 从答案的 Sources 部分提取 gold 文件名
    gold_filenames = []
    if a_file.exists():
        a_text = a_file.read_text(encoding="utf-8")
        # 匹配 Sources 部分的文件引用
        in_sources = False
        for line in a_text.split("\n"):
            if "## Sources" in line:
                in_sources = True
                continue
            if in_sources and line.startswith("## "):
                break
            if in_sources:
                # 提取 `raw/...` 或文件名
                matches = re.findall(r"`(?:raw/)?(?:\w+/)*([^`/]+?\.\w+)`", line)
                gold_filenames.extend(matches)

    # 去重
    gold_filenames = list(dict.fromkeys(gold_filenames))
    return query, gold_filenames


# ── 诊断主流程 ────────────────────────────────────────────────

async def run_diagnosis(query: str, gold_filenames: list[str]) -> DiagReport:
    """运行完整诊断流程。"""
    from src.core.bm25_index import bm25_index
    from src.core.query_rewriter import query_rewriter
    from src.core.reranker import get_reranker
    from src.core.search import (
        VECTOR_TOP_K, EXPANDED_TOP_K, BM25_TOP_K,
        RERANKER_TOP_N, RERANKER_THRESHOLD, RRF_K,
        vector_search, rrf_fuse, reranker_filter,
        iterative_expand, graph_enrich, related_docs_search,
        _multi_query_rerank,
    )
    from src.db.postgres import async_session_factory
    from src.db.neo4j_client import Neo4jClient
    from src.pipeline.embedder import embedder

    report = DiagReport(query=query, gold_filenames=gold_filenames)
    t_total = time.monotonic()

    neo4j = Neo4jClient()

    async with async_session_factory() as session:

        # ── ① Query 改写 ─────────────────────────────────────
        stage = StageMetrics(name="① Query改写")
        t0 = time.monotonic()
        try:
            rewrite_result = await query_rewriter.rewrite(query)
        except Exception:
            from src.core.query_rewriter import RewriteResult
            rewrite_result = RewriteResult(
                rewritten_query=query, keywords=[query], expanded_queries=[]
            )
        stage.elapsed_ms = (time.monotonic() - t0) * 1000

        rewritten = rewrite_result.rewritten_query
        keywords = rewrite_result.keywords
        expanded = rewrite_result.expanded_queries
        sub_queries = rewrite_result.sub_queries

        stage.extra = {
            "rewritten": rewritten[:80],
            "keywords": keywords[:5],
            "expanded_count": len(expanded),
            "sub_queries": sub_queries,
            "complexity": "multi_constraint" if sub_queries else "simple",
        }
        report.stages.append(stage)

        # ── ② 多路召回 ───────────────────────────────────────
        stage = StageMetrics(name="② 多路召回")
        t0 = time.monotonic()

        # 逐路执行并追踪目标
        path_hits = {}

        # L1a: 主查询向量
        main_results = await vector_search(session, rewritten, VECTOR_TOP_K)
        rank, score = _find_target_rank(main_results, gold_filenames)
        if rank > 0:
            path_hits["vector_main"] = (rank, score)

        # L1b: 扩展查询向量
        expanded_results = []
        for i, eq in enumerate(expanded[:2]):
            er = await vector_search(session, eq, EXPANDED_TOP_K)
            expanded_results.extend(er)
            rank, score = _find_target_rank(er, gold_filenames)
            if rank > 0:
                path_hits[f"expanded_{i+1}"] = (rank, score)

        # L1c: BM25
        bm25_results = bm25_index.search(
            " ".join(keywords) if keywords else query, BM25_TOP_K
        )
        rank, score = _find_target_rank(bm25_results, gold_filenames)
        if rank > 0:
            path_hits["bm25"] = (rank, score)

        # L1d: 子问题向量
        sub_candidates_lists = []
        for i, sq in enumerate(sub_queries[:4]):
            sr = await vector_search(session, sq, EXPANDED_TOP_K)
            if sr:
                sub_candidates_lists.append(sr)
                rank, score = _find_target_rank(sr, gold_filenames)
                if rank > 0:
                    path_hits[f"sub_query_{i+1}"] = (rank, score)

        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.path_hits = path_hits
        stage.target_hit = len(path_hits) > 0
        stage.total_candidates = (
            len(main_results) + len(expanded_results) +
            len(bm25_results) + sum(len(s) for s in sub_candidates_lists)
        )
        stage.extra = {
            "vector_main": len(main_results),
            "vector_expanded": len(expanded_results),
            "bm25": len(bm25_results),
            "sub_queries_paths": len(sub_candidates_lists),
        }
        report.stages.append(stage)

        # ── ③ RRF 融合 ───────────────────────────────────────
        stage = StageMetrics(name="③ RRF融合")
        t0 = time.monotonic()

        ranked_lists = [main_results]
        if expanded_results:
            ranked_lists.append(expanded_results)
        if bm25_results:
            ranked_lists.append(bm25_results)
        for sub_list in sub_candidates_lists:
            ranked_lists.append(sub_list)

        merged = rrf_fuse(ranked_lists)
        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.total_candidates = len(merged)

        rank, score = _find_target_rank(merged, gold_filenames)
        stage.target_hit = rank > 0
        stage.target_rank = rank
        stage.target_score = score

        # 计算投票数
        if rank > 0:
            target_chunk = None
            for c in merged:
                if _is_target(c, gold_filenames):
                    target_chunk = c
                    break
            vote_count = 0
            if target_chunk:
                cid = target_chunk["chunk_id"]
                for rl in ranked_lists:
                    if any(ch["chunk_id"] == cid for ch in rl):
                        vote_count += 1
            stage.extra = {"vote_count": vote_count, "noise_above": rank - 1}
        report.stages.append(stage)

        # ── ④ 迭代多跳扩展 ───────────────────────────────────
        stage = StageMetrics(name="④ 迭代多跳")
        t0 = time.monotonic()

        target_before = _find_target_rank(merged, gold_filenames)[0]
        second_round = []
        try:
            second_round = await iterative_expand(neo4j, session, merged, rewritten)
            if second_round:
                for c in second_round:
                    c["rrf_score"] = c.get("score", 0.0) * 0.3
                merged = merged + second_round
        except Exception:
            pass

        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.total_candidates = len(second_round)

        target_after = _find_target_rank(merged, gold_filenames)[0]
        # 判断目标是首轮已有还是扩展新增
        target_in_expand = _find_target_rank(second_round, gold_filenames)[0] > 0 if second_round else False
        stage.target_hit = target_after > 0
        stage.target_rank = target_after
        stage.extra = {
            "expand_added": len(second_round),
            "target_source": "扩展新增" if target_in_expand else "首轮已有",
        }
        report.stages.append(stage)

        # ── ⑤ Reranker 守门 ──────────────────────────────────
        stage = StageMetrics(name="⑤ Reranker")
        t0 = time.monotonic()

        rank_before_rerank = _find_target_rank(merged, gold_filenames)[0]
        survivors = []
        try:
            if sub_queries:
                survivors = await _multi_query_rerank(
                    rewritten, sub_queries, merged, RERANKER_THRESHOLD, RERANKER_TOP_N
                )
            else:
                survivors = await reranker_filter(
                    rewritten, merged, RERANKER_THRESHOLD, RERANKER_TOP_N
                )
        except Exception:
            survivors = merged[:RERANKER_TOP_N]

        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.total_candidates = len(survivors)

        rank, score = _find_target_rank(survivors, gold_filenames)
        stage.target_hit = rank > 0
        stage.target_rank = rank
        stage.target_score = score
        stage.extra = {
            "input_count": len(merged),
            "survivors": len(survivors),
            "rank_before": rank_before_rerank,
            "rank_after": rank,
            "mode": "multi-query" if sub_queries else "single",
            "survive": rank > 0,
        }
        report.stages.append(stage)

        # ── ⑥ 图谱增强 ───────────────────────────────────────
        stage = StageMetrics(name="⑥ 图谱增强")
        t0 = time.monotonic()

        graph_chunks = []
        related_entities = []
        try:
            related_entities, graph_chunks = await graph_enrich(neo4j, survivors, session)
        except Exception:
            pass

        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.total_candidates = len(graph_chunks)

        rank, score = _find_target_rank(graph_chunks, gold_filenames)
        stage.target_hit = rank > 0
        stage.target_rank = rank
        stage.extra = {
            "entities": len(related_entities),
            "graph_chunks": len(graph_chunks),
            "graph_rescue": rank > 0 and _find_target_rank(survivors, gold_filenames)[0] < 0,
        }
        report.stages.append(stage)

        # ── ⑦ 关联文档补充 ───────────────────────────────────
        stage = StageMetrics(name="⑦ 关联文档")
        t0 = time.monotonic()

        related_doc_chunks = []
        try:
            existing_doc_ids = {c["doc_id"] for c in survivors}
            query_embedding = await embedder.embed_text(rewritten)
            related_doc_chunks = await related_docs_search(
                neo4j, session, existing_doc_ids, query_embedding, top_k=5
            )
        except Exception:
            pass

        stage.elapsed_ms = (time.monotonic() - t0) * 1000
        stage.total_candidates = len(related_doc_chunks)

        rank, score = _find_target_rank(related_doc_chunks, gold_filenames)
        stage.target_hit = rank > 0
        stage.target_rank = rank
        stage.extra = {"related_doc_chunks": len(related_doc_chunks)}
        report.stages.append(stage)

        # ── 最终合并 ─────────────────────────────────────────
        all_survivors = list(survivors)
        supplementary = graph_chunks + related_doc_chunks
        if supplementary:
            survivor_ids = {c["chunk_id"] for c in survivors}
            supp_unique = []
            supp_seen = set()
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
                except Exception:
                    scored_supp = supp_unique
                all_survivors = survivors + scored_supp

        # 去重排序
        seen_ids = set()
        deduped = []
        for c in all_survivors:
            cid = c["chunk_id"]
            if cid not in seen_ids:
                seen_ids.add(cid)
                deduped.append(c)
        deduped.sort(key=lambda x: -float(x.get("reranker_score", x.get("rrf_score", 0.0))))

        final_rank, final_score = _find_target_rank(deduped, gold_filenames)
        report.final_rank = final_rank
        if final_rank > 0 and final_rank <= 5:
            report.verdict = "✓ top-5"
        elif final_rank > 0 and final_rank <= 15:
            report.verdict = "△ top-15"
        elif final_rank > 0:
            report.verdict = f"⚠ rank={final_rank}"
        else:
            report.verdict = "✗ LOST"

        # ── 内容质量分析 ─────────────────────────────────────
        report.content_quality = await _analyze_content_quality(
            session, gold_filenames, query
        )

    report.total_ms = (time.monotonic() - t_total) * 1000
    return report


async def _analyze_content_quality(session, gold_filenames: list[str], query: str) -> dict:
    """分析目标文档的分块内容质量。"""
    from sqlalchemy import select
    from src.db.models import Chunk, Document

    quality = {}
    try:
        # 查找目标文档的所有 chunks
        stmt = select(Chunk).join(Document, Chunk.doc_id == Document.id).where(
            Document.file_status == "active"
        )
        result = await session.execute(stmt)
        rows = result.all()

        target_chunks = []
        for (chunk,) in rows:
            filename = chunk.doc_uri.split(":", 1)[-1] if ":" in (chunk.doc_uri or "") else (chunk.doc_uri or "")
            if any(g in filename for g in gold_filenames):
                target_chunks.append(chunk)

        if not target_chunks:
            quality["error"] = "目标文档未找到 chunks"
            return quality

        quality["target_chunk_count"] = len(target_chunks)
        quality["chunk_token_sizes"] = [
            len(c.chunk_text.split()) for c in target_chunks
        ]

        # overview 语言检测
        overview_langs = []
        overview_faithfulness_issues = []
        for c in target_chunks:
            ov = c.overview or ""
            if ov:
                # 简单判断：中文字符占比 > 30% 则为中文
                zh_chars = sum(1 for ch in ov if '\u4e00' <= ch <= '\u9fff')
                lang = "zh" if zh_chars > len(ov) * 0.2 else "en"
                overview_langs.append(lang)

                # 检查 overview 是否包含 chunk 中的关键实体（大写词）
                chunk_entities = set(re.findall(r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b', c.chunk_text))
                ov_lower = ov.lower()
                missing = [e for e in chunk_entities if e.lower() not in ov_lower]
                if chunk_entities and len(missing) > len(chunk_entities) * 0.5:
                    overview_faithfulness_issues.append({
                        "chunk_index": c.chunk_index,
                        "missing_entities": missing[:5],
                    })

        quality["overview_languages"] = overview_langs
        quality["overview_lang_mismatch"] = any(l == "zh" for l in overview_langs)
        quality["overview_faithfulness_issues"] = overview_faithfulness_issues[:3]

        # 跨 chunk 依赖检测（简单版：检查关键信息是否分散）
        quality["cross_chunk_dependency"] = len(target_chunks) > 1

    except Exception as e:
        quality["error"] = str(e)

    return quality


# ── 报告格式化 ────────────────────────────────────────────────

def format_report(report: DiagReport) -> str:
    """格式化诊断报告为可读文本。"""
    lines = []
    sep = "═" * 60

    lines.append(sep)
    lines.append(f"Query: {report.query[:70]}...")
    lines.append(f"Gold:  {', '.join(report.gold_filenames)}")
    lines.append(f"Verdict: {report.verdict} | Final rank: {report.final_rank}")
    lines.append(sep)

    # ── 漏斗追踪 ──
    lines.append("")
    lines.append("【漏斗追踪】")
    for stage in report.stages:
        hit_mark = "✓" if stage.target_hit else "✗"
        rank_str = f"rank={stage.target_rank}" if stage.target_rank > 0 else "MISS"
        score_str = f"score={stage.target_score:.4f}" if stage.target_score > 0 else ""

        line = f"  {stage.name}: {hit_mark} {rank_str} {score_str}"

        # 路径级详情（召回段）
        if stage.path_hits:
            paths = []
            for pname, (prank, pscore) in stage.path_hits.items():
                paths.append(f"{pname}=HIT@{prank}")
            # 标记未命中的路径
            all_paths = ["vector_main", "expanded", "bm25", "sub_queries"]
            line += f" | {' '.join(paths)}"

        # 额外信息
        if stage.extra:
            extras = []
            for k, v in stage.extra.items():
                if k in ("vote_count", "noise_above", "mode", "survive",
                         "target_source", "expand_added", "graph_rescue"):
                    extras.append(f"{k}={v}")
            if extras:
                line += f" | {' '.join(extras)}"

        lines.append(line)

    # ── 耗时分布 ──
    lines.append("")
    lines.append(f"【耗时分布】 总计 {report.total_ms:.0f}ms")
    max_ms = max((s.elapsed_ms for s in report.stages), default=1)
    for stage in report.stages:
        pct = stage.elapsed_ms / report.total_ms * 100 if report.total_ms > 0 else 0
        bar_len = int(stage.elapsed_ms / max_ms * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        bottleneck = " ⚠️" if stage.elapsed_ms == max_ms else ""
        lines.append(
            f"  {stage.name:12s} {bar} {stage.elapsed_ms:7.0f}ms ({pct:4.1f}%){bottleneck}"
        )

    # ── 内容质量 ──
    lines.append("")
    lines.append("【内容质量】")
    cq = report.content_quality
    if "error" in cq:
        lines.append(f"  ⚠ {cq['error']}")
    else:
        lines.append(f"  目标 chunks 数: {cq.get('target_chunk_count', '?')}")
        sizes = cq.get("chunk_token_sizes", [])
        if sizes:
            lines.append(f"  chunk 词数: {sizes}")
        langs = cq.get("overview_languages", [])
        if langs:
            mismatch = "⚠ 语言不匹配" if cq.get("overview_lang_mismatch") else "✓"
            lines.append(f"  overview 语言: {langs} {mismatch}")
        issues = cq.get("overview_faithfulness_issues", [])
        if issues:
            lines.append(f"  overview 忠实度问题: {len(issues)} 处")
            for iss in issues[:2]:
                lines.append(f"    chunk#{iss['chunk_index']}: 缺失 {iss['missing_entities']}")
        if cq.get("cross_chunk_dependency"):
            lines.append(f"  跨 chunk 依赖: YES (信息分散在多个 chunk)")

    lines.append(sep)
    return "\n".join(lines)


def format_summary(reports: list[DiagReport]) -> str:
    """批量诊断的汇总表格。"""
    lines = []
    lines.append("=" * 90)
    lines.append(f"{'Q':>3} | {'Verdict':10s} | {'Rank':>4} | {'②召回':6s} | {'③RRF':>5} | {'⑤Rerank':7s} | {'Total':>7} | Gold")
    lines.append("-" * 90)

    for r in reports:
        # 提取各段关键指标
        recall_stage = next((s for s in r.stages if "召回" in s.name), None)
        rrf_stage = next((s for s in r.stages if "RRF" in s.name), None)
        rerank_stage = next((s for s in r.stages if "Reranker" in s.name), None)

        recall_str = "HIT" if (recall_stage and recall_stage.target_hit) else "MISS"
        rrf_str = str(rrf_stage.target_rank) if (rrf_stage and rrf_stage.target_rank > 0) else "-"
        rerank_str = str(rerank_stage.target_rank) if (rerank_stage and rerank_stage.target_rank > 0) else "LOST"

        qid = ""
        gold_str = ", ".join(r.gold_filenames[:2])

        lines.append(
            f"{qid:>3} | {r.verdict:10s} | {r.final_rank:>4} | {recall_str:6s} | "
            f"{rrf_str:>5} | {rerank_str:7s} | {r.total_ms:6.0f}ms | {gold_str}"
        )

    lines.append("=" * 90)

    # 统计
    total = len(reports)
    top5 = sum(1 for r in reports if r.final_rank > 0 and r.final_rank <= 5)
    top15 = sum(1 for r in reports if r.final_rank > 0 and r.final_rank <= 15)
    lost = sum(1 for r in reports if r.final_rank < 0)
    recall_miss = sum(
        1 for r in reports
        if not next((s for s in r.stages if "召回" in s.name), StageMetrics(name="")).target_hit
    )

    lines.append(f"\n汇总: {total} 题 | top-5={top5} | top-15={top15} | lost={lost} | 召回MISS={recall_miss}")
    return "\n".join(lines)


# ── CLI 入口 ──────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="检索管线逐段诊断工具")
    parser.add_argument("--query", type=str, help="自定义查询")
    parser.add_argument("--gold", type=str, help="目标文件名（逗号分隔）")
    parser.add_argument("--bench", type=str, help="Benchmark 题号（如 13）或 'all'")
    parser.add_argument("--summary", action="store_true", help="批量模式输出汇总表")
    args = parser.parse_args()

    if args.bench:
        if args.bench.lower() == "all":
            # 批量跑所有 40 题
            reports = []
            for qid in range(1, 41):
                try:
                    query, gold = _load_bench_question(qid)
                    if not gold:
                        print(f"  Q{qid:02d}: 无 gold 文件，跳过")
                        continue
                    print(f"  诊断 Q{qid:02d}...", end=" ", flush=True)
                    report = await run_diagnosis(query, gold)
                    reports.append(report)
                    print(report.verdict)
                except Exception as e:
                    print(f"  Q{qid:02d}: 错误 - {e}")
            print("\n" + format_summary(reports))
        else:
            qid = int(args.bench)
            query, gold = _load_bench_question(qid)
            print(f"Q{qid} gold files: {gold}\n")
            report = await run_diagnosis(query, gold)
            print(format_report(report))
    elif args.query:
        gold = args.gold.split(",") if args.gold else []
        if not gold:
            print("⚠ 未指定 --gold，将只输出耗时信息，无法追踪目标")
        report = await run_diagnosis(args.query, gold)
        print(format_report(report))
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
