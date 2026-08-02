/**
 * 检索侧全量指标采集脚本（单次调用检索管线，非 agentic 循环）
 * 用法: node --experimental-strip-types infra/retrieval-metrics.ts
 *
 * 指标清单:
 *  1. 命中率对比 (top-1/3/5/10)：融合 / 纯BM25 / 纯向量
 *  2. MRR & NDCG@5
 *  3. Precision@5（top-5 中来自正确文档的比例）
 *  4. 答案关键词覆盖率（检索到的 chunk 里包含多少 expected_answer_keywords）
 *  5. 检索区分度（正确结果 vs 最强错误结果的分数差）
 *  6. 向量相似度对比（正确 chunk sim vs 最强错误 chunk sim）
 *  7. 信号贡献统计
 *  8. 按难度分组
 *  9. 检索延迟
 * 10. 逐条明细
 */
import fs from "node:fs";
import path from "node:path";
import { loadConfig } from "../src/config.ts";
import { hybridSearch, bm25Search, vectorSearch } from "../src/core/search.ts";
import type { SearchHit } from "../src/core/search.ts";
import { closePool } from "../src/core/db.ts";

const config = loadConfig();
const QA_PATH = path.join(import.meta.dirname, "qa-test-set.json");

interface QaItem {
  id: number;
  query: string;
  expected_source: string;
  expected_answer_keywords: string[];
  difficulty: string;
}

interface QueryResult {
  id: number;
  query: string;
  difficulty: string;
  expectedSource: string;
  keywords: string[];
  // 排名（-1 = 未命中）
  fusedRank: number;
  bm25Rank: number;
  vectorRank: number;
  // 命中信号
  signals: string[];
  // 区分度
  fusedScoreMargin: number; // 正确 RRF 分 - 最强错误 RRF 分
  vectorSimCorrect: number; // 正确 chunk 的 cosine sim
  vectorSimBestWrong: number; // 最强错误 chunk 的 cosine sim
  // 关键词覆盖
  keywordHitCount: number;
  keywordTotal: number;
  // Precision@5
  precisionAt5: number;
  // 延迟
  latencyMs: number;
}

function findRank(hits: SearchHit[], expectedSource: string): number {
  const stem = expectedSource.replace(/\.\w+$/, "");
  for (let i = 0; i < hits.length; i++) {
    if (hits[i].sourcePath.includes(stem)) return i + 1;
  }
  return -1;
}

function isCorrect(hit: SearchHit, expectedSource: string): boolean {
  return hit.sourcePath.includes(expectedSource.replace(/\.\w+$/, ""));
}

/** NDCG@K: 二值相关性（来自正确文档=1，否则=0） */
function ndcgAtK(hits: SearchHit[], expectedSource: string, k: number): number {
  let dcg = 0;
  for (let i = 0; i < Math.min(hits.length, k); i++) {
    if (isCorrect(hits[i], expectedSource)) {
      dcg += 1 / Math.log2(i + 2); // i+2 因为 log2(1)=0
    }
  }
  // IDCG: 理想情况第1位就命中 → 1/log2(2) = 1
  const idcg = 1;
  return dcg / idcg;
}

async function main() {
  console.log("=== 检索侧全量指标 ===\n");

  const qaSet: QaItem[] = JSON.parse(fs.readFileSync(QA_PATH, "utf8"));
  const results: QueryResult[] = [];
  const MAX_K = 10; // 取 top-10 用于多 K 分析

  for (const qa of qaSet) {
    const t0 = performance.now();
    const fusedHits = await hybridSearch(config, qa.query, { limit: MAX_K });
    const latencyMs = performance.now() - t0;

    const [bm25Hits, vectorHits] = await Promise.all([
      bm25Search(config, qa.query, MAX_K),
      vectorSearch(config, qa.query, MAX_K),
    ]);

    const fusedRank = findRank(fusedHits, qa.expected_source);
    const bm25Rank = findRank(bm25Hits, qa.expected_source);
    const vectorRank = findRank(vectorHits, qa.expected_source);

    // 信号来源
    const hitEntry = fusedHits.find((h) => isCorrect(h, qa.expected_source));
    const signals = hitEntry?.signals ?? [];

    // 检索区分度（RRF 分数差）
    const correctScore = hitEntry?.score ?? 0;
    const bestWrongScore = Math.max(
      ...fusedHits.filter((h) => !isCorrect(h, qa.expected_source)).map((h) => h.score),
      0,
    );
    const fusedScoreMargin = correctScore - bestWrongScore;

    // 向量相似度对比
    const correctVec = vectorHits.find((h) => isCorrect(h, qa.expected_source));
    const wrongVecs = vectorHits.filter((h) => !isCorrect(h, qa.expected_source));
    const vectorSimCorrect = correctVec?.score ?? 0;
    const vectorSimBestWrong = wrongVecs.length > 0 ? wrongVecs[0].score : 0;

    // 答案关键词覆盖率（在 top-5 融合结果的文本中查找）
    const top5Content = fusedHits.slice(0, 5).map((h) => h.content).join("\n");
    let keywordHitCount = 0;
    for (const kw of qa.expected_answer_keywords) {
      if (top5Content.includes(kw)) keywordHitCount++;
    }

    // Precision@5
    const top5 = fusedHits.slice(0, 5);
    const correctInTop5 = top5.filter((h) => isCorrect(h, qa.expected_source)).length;
    const precisionAt5 = top5.length > 0 ? correctInTop5 / top5.length : 0;

    results.push({
      id: qa.id,
      query: qa.query,
      difficulty: qa.difficulty,
      expectedSource: qa.expected_source,
      keywords: qa.expected_answer_keywords,
      fusedRank,
      bm25Rank,
      vectorRank,
      signals,
      fusedScoreMargin,
      vectorSimCorrect,
      vectorSimBestWrong,
      keywordHitCount,
      keywordTotal: qa.expected_answer_keywords.length,
      precisionAt5,
      latencyMs,
    });
  }

  const total = results.length;

  // ─── 1. 多 K 命中率 ───
  console.log("【1. 命中率对比（多 K）】");
  for (const k of [1, 3, 5, 10]) {
    const fusedK = results.filter((r) => r.fusedRank > 0 && r.fusedRank <= k).length;
    const bm25K = results.filter((r) => r.bm25Rank > 0 && r.bm25Rank <= k).length;
    const vecK = results.filter((r) => r.vectorRank > 0 && r.vectorRank <= k).length;
    console.log(`  top-${String(k).padStart(2)}: 融合=${fusedK}/${total}(${(fusedK / total * 100).toFixed(0)}%) | BM25=${bm25K}/${total}(${(bm25K / total * 100).toFixed(0)}%) | 向量=${vecK}/${total}(${(vecK / total * 100).toFixed(0)}%)`);
  }
  console.log();

  // ─── 2. MRR & NDCG@5 ───
  const mrr = results.reduce((s, r) => s + (r.fusedRank > 0 ? 1 / r.fusedRank : 0), 0) / total;
  const ndcg5 = results.reduce((s, r) => {
    // 需要重新算 NDCG，这里用 fusedRank 近似
    if (r.fusedRank <= 0) return s;
    if (r.fusedRank > 5) return s;
    return s + 1 / Math.log2(r.fusedRank + 1);
  }, 0) / total;
  console.log("【2. 排序质量】");
  console.log(`  MRR:     ${mrr.toFixed(3)}`);
  console.log(`  NDCG@5:  ${ndcg5.toFixed(3)}`);
  console.log();

  // ─── 3. Precision@5 ───
  const avgPrecision = results.reduce((s, r) => s + r.precisionAt5, 0) / total;
  console.log("【3. Precision@5（top-5 中来自正确文档的比例）】");
  console.log(`  平均: ${(avgPrecision * 100).toFixed(1)}%`);
  console.log();

  // ─── 4. 答案关键词覆盖率 ───
  const totalKw = results.reduce((s, r) => s + r.keywordTotal, 0);
  const hitKw = results.reduce((s, r) => s + r.keywordHitCount, 0);
  console.log("【4. 答案关键词覆盖率（top-5 chunks 中包含 expected_answer_keywords 的比例）】");
  console.log(`  总关键词: ${totalKw} | 命中: ${hitKw} | 覆盖率: ${(hitKw / totalKw * 100).toFixed(1)}%`);
  // 逐条
  for (const r of results) {
    const pct = (r.keywordHitCount / r.keywordTotal * 100).toFixed(0);
    const flag = r.keywordHitCount < r.keywordTotal ? " ⚠" : "";
    console.log(`  #${String(r.id).padStart(2)} ${r.keywordHitCount}/${r.keywordTotal} (${pct}%)${flag} | ${r.query.slice(0, 25)}`);
  }
  console.log();

  // ─── 5. 检索区分度 ───
  const margins = results.filter((r) => r.fusedRank > 0).map((r) => r.fusedScoreMargin);
  const avgMargin = margins.reduce((a, b) => a + b, 0) / margins.length;
  const minMargin = Math.min(...margins);
  console.log("【5. 检索区分度（正确 RRF 分 - 最强错误 RRF 分）】");
  console.log(`  平均 margin: ${avgMargin.toFixed(5)}`);
  console.log(`  最小 margin: ${minMargin.toFixed(5)}（越小越危险）`);
  console.log();

  // ─── 6. 向量相似度对比 ───
  const simMargins = results.map((r) => ({
    id: r.id,
    correct: r.vectorSimCorrect,
    bestWrong: r.vectorSimBestWrong,
    margin: r.vectorSimCorrect - r.vectorSimBestWrong,
  }));
  const avgSimMargin = simMargins.reduce((s, m) => s + m.margin, 0) / total;
  const minSimMargin = Math.min(...simMargins.map((m) => m.margin));
  console.log("【6. 向量相似度对比（正确 chunk sim - 最强错误 chunk sim）】");
  console.log(`  平均 sim margin: ${avgSimMargin.toFixed(4)}`);
  console.log(`  最小 sim margin: ${minSimMargin.toFixed(4)}`);
  // 最危险的 3 条
  const sorted = [...simMargins].sort((a, b) => a.margin - b.margin);
  console.log("  最危险的 3 条:");
  for (const m of sorted.slice(0, 3)) {
    console.log(`    #${m.id}: 正确=${m.correct.toFixed(3)} 错误=${m.bestWrong.toFixed(3)} 差=${m.margin.toFixed(3)}`);
  }
  console.log();

  // ─── 7. 信号贡献 ───
  const signalCount: Record<string, number> = {};
  for (const r of results) {
    for (const s of r.signals) signalCount[s] = (signalCount[s] ?? 0) + 1;
  }
  console.log("【7. 命中信号来源】");
  for (const [sig, count] of Object.entries(signalCount)) console.log(`  ${sig}: ${count} 次`);
  console.log();

  // ─── 8. 按难度分组 ───
  const difficulties = [...new Set(results.map((r) => r.difficulty))];
  console.log("【8. 按难度分组】");
  for (const diff of difficulties) {
    const group = results.filter((r) => r.difficulty === diff);
    const hitCount = group.filter((r) => r.fusedRank > 0 && r.fusedRank <= 5).length;
    const groupMrr = group.reduce((s, r) => s + (r.fusedRank > 0 ? 1 / r.fusedRank : 0), 0) / group.length;
    const kwTotal = group.reduce((s, r) => s + r.keywordTotal, 0);
    const kwHit = group.reduce((s, r) => s + r.keywordHitCount, 0);
    console.log(`  ${diff.padEnd(7)}: 命中=${hitCount}/${group.length} | MRR=${groupMrr.toFixed(3)} | 关键词覆盖=${kwHit}/${kwTotal}(${(kwHit / kwTotal * 100).toFixed(0)}%)`);
  }
  console.log();

  // ─── 9. 延迟 ───
  const latencies = results.map((r) => r.latencyMs);
  const avgLat = latencies.reduce((a, b) => a + b, 0) / total;
  const sortedLat = [...latencies].sort((a, b) => a - b);
  const p50 = sortedLat[Math.floor(total * 0.5)];
  const p95 = sortedLat[Math.floor(total * 0.95)];
  console.log("【9. 检索延迟】");
  console.log(`  平均: ${avgLat.toFixed(0)}ms | P50: ${p50.toFixed(0)}ms | P95: ${p95.toFixed(0)}ms | 最慢: ${Math.max(...latencies).toFixed(0)}ms`);
  console.log();

  // ─── 10. 逐条明细 ───
  console.log("【10. 逐条明细】");
  console.log("  #  | 融合 | BM25 | 向量 | 关键词  | P@5  | sim差  | 信号      | 查询");
  console.log("  " + "-".repeat(100));
  for (const r of results) {
    const fR = r.fusedRank > 0 ? `#${r.fusedRank}` : "MISS";
    const bR = r.bm25Rank > 0 ? `#${r.bm25Rank}` : "MISS";
    const vR = r.vectorRank > 0 ? `#${r.vectorRank}` : "MISS";
    const kw = `${r.keywordHitCount}/${r.keywordTotal}`;
    const p5 = (r.precisionAt5 * 100).toFixed(0) + "%";
    const simM = (r.vectorSimCorrect - r.vectorSimBestWrong).toFixed(3);
    const sigs = r.signals.join("+") || "-";
    console.log(`  ${String(r.id).padStart(2)} | ${fR.padStart(4)} | ${bR.padStart(4)} | ${vR.padStart(4)} | ${kw.padStart(5)} | ${p5.padStart(4)} | ${simM.padStart(6)} | ${sigs.padEnd(9)} | ${r.query.slice(0, 25)}`);
  }
  console.log();

  await closePool();
  console.log("=== 完成 ===");
}

main().catch((err) => {
  console.error("检索指标脚本执行失败:", err);
  process.exit(1);
});
