/**
 * 图谱效果 A/B 对比：同一组 query，有图谱 vs 无图谱
 * 用法: node --experimental-strip-types infra/graph-ab-test.ts
 */
import fs from "node:fs";
import path from "node:path";
import { loadConfig } from "../src/config.ts";
import { hybridSearch } from "../src/core/search.ts";
import type { SearchHit } from "../src/core/search.ts";
import { closePool } from "../src/core/db.ts";

const config = loadConfig();
const QA_PATH = path.join(import.meta.dirname, "qa-test-set.json");

interface QaItem {
  id: number;
  query: string;
  expected_source: string;
  expected_answer_keywords?: string[];
}

const qa: QaItem[] = JSON.parse(fs.readFileSync(QA_PATH, "utf8"));

function hitAt(hits: SearchHit[], source: string, k: number): number {
  for (let i = 0; i < Math.min(k, hits.length); i++) {
    if (hits[i].sourcePath.includes(source)) return i + 1;
  }
  return -1;
}

async function run() {
  console.log(`=== 图谱 A/B 对比（${qa.length} 条 query）===\n`);

  let withGraphHit1 = 0, noGraphHit1 = 0;
  let withGraphHit5 = 0, noGraphHit5 = 0;
  let withGraphMRR = 0, noGraphMRR = 0;
  let graphSignalCount = 0;
  let improved = 0, degraded = 0, same = 0;
  const details: string[] = [];

  for (const item of qa) {
    const t0 = Date.now();
    const [withGraph, noGraph] = await Promise.all([
      hybridSearch(config, item.query, { limit: 10 }),
      hybridSearch(config, item.query, { limit: 10, noGraph: true }),
    ]);
    const latency = Date.now() - t0;

    // 命中率
    const wgRank = hitAt(withGraph, item.expected_source, 10);
    const ngRank = hitAt(noGraph, item.expected_source, 10);

    if (wgRank === 1) withGraphHit1++;
    if (ngRank === 1) noGraphHit1++;
    if (wgRank > 0 && wgRank <= 5) withGraphHit5++;
    if (ngRank > 0 && ngRank <= 5) noGraphHit5++;

    // MRR
    if (wgRank > 0) withGraphMRR += 1 / wgRank;
    if (ngRank > 0) noGraphMRR += 1 / ngRank;

    // 图谱信号统计
    const graphHits = withGraph.filter((h) => h.signals.includes("graph"));
    if (graphHits.length > 0) graphSignalCount++;

    // 排名变化
    if (wgRank > 0 && ngRank > 0) {
      if (wgRank < ngRank) improved++;
      else if (wgRank > ngRank) degraded++;
      else same++;
    } else if (wgRank > 0 && ngRank < 0) {
      improved++; // 图谱独立救回
    } else if (wgRank < 0 && ngRank > 0) {
      degraded++;
    } else {
      same++;
    }

    const delta = wgRank === ngRank ? "=" : wgRank < ngRank ? `↑${ngRank - wgRank}` : `↓${wgRank - ngRank}`;
    const graphTag = graphHits.length > 0 ? ` [graph:${graphHits.length}hits]` : "";
    details.push(
      `#${String(item.id).padStart(2)} rank ${ngRank < 0 ? "MISS" : ngRank} → ${wgRank < 0 ? "MISS" : wgRank} ${delta}${graphTag}  ${item.query.slice(0, 30)}`,
    );
  }

  const n = qa.length;
  console.log("【命中率对比】");
  console.log(`  top-1: 无图谱 ${noGraphHit1}/${n} (${(noGraphHit1 / n * 100).toFixed(1)}%)  →  有图谱 ${withGraphHit1}/${n} (${(withGraphHit1 / n * 100).toFixed(1)}%)`);
  console.log(`  top-5: 无图谱 ${noGraphHit5}/${n} (${(noGraphHit5 / n * 100).toFixed(1)}%)  →  有图谱 ${withGraphHit5}/${n} (${(withGraphHit5 / n * 100).toFixed(1)}%)`);
  console.log();
  console.log("【MRR】");
  console.log(`  无图谱: ${(noGraphMRR / n).toFixed(4)}`);
  console.log(`  有图谱: ${(withGraphMRR / n).toFixed(4)}`);
  console.log(`  差值:   ${((withGraphMRR - noGraphMRR) / n).toFixed(4)}`);
  console.log();
  console.log("【图谱贡献】");
  console.log(`  图谱信号出现的 query 数: ${graphSignalCount}/${n}`);
  console.log(`  排名提升: ${improved}  排名下降: ${degraded}  不变: ${same}`);
  console.log();
  console.log("【逐条明细】（rank 变化：无图谱 → 有图谱）");
  for (const d of details) console.log(`  ${d}`);

  await closePool();
}

run().catch((e) => { console.error(e); process.exit(1); });
