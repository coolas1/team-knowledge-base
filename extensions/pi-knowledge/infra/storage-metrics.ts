/**
 * 存储侧纯代码指标采集脚本
 * 用法: node --experimental-strip-types infra/storage-metrics.ts
 *
 * 指标:
 *  1. 写入成功率 & 文档/分块基础统计
 *  2. 分块质量（长度分布、空块）
 *  3. BM25 全文检索命中率（qa-test-set top-k）
 *  4. 向量检索命中率（qa-test-set top-k）
 *  5. 图谱任务状态
 */
import fs from "node:fs";
import path from "node:path";
import pg from "pg";
import { loadConfig } from "../src/config.ts";
import { embed, embedOne } from "../src/core/embedding.ts";
import { toVectorLiteral } from "../src/core/db.ts";

const config = loadConfig();
const pool = new pg.Pool({
  host: config.pg.host,
  port: config.pg.port,
  user: config.pg.user,
  password: config.pg.password,
  database: config.pg.database,
  max: 3,
});

const QA_PATH = path.join(import.meta.dirname, "qa-test-set.json");
const TOP_K = 5;

interface QaItem {
  id: number;
  query: string;
  expected_source: string;
  difficulty: string;
}

async function main() {
  console.log("=== 存储侧纯代码指标 ===\n");

  // ─── 1. 基础统计 ───
  const docCount = await pool.query("SELECT count(*)::int AS n FROM documents");
  const chunkCount = await pool.query("SELECT count(*)::int AS n FROM chunks");
  const nullEmbed = await pool.query(
    "SELECT count(*)::int AS n FROM chunks WHERE embedding IS NULL",
  );
  console.log("【1. 写入统计】");
  console.log(`  文档数: ${docCount.rows[0].n}`);
  console.log(`  分块数: ${chunkCount.rows[0].n}`);
  console.log(`  缺失 embedding 的块: ${nullEmbed.rows[0].n}`);
  console.log(`  embedding 覆盖率: ${(((chunkCount.rows[0].n - nullEmbed.rows[0].n) / chunkCount.rows[0].n) * 100).toFixed(1)}%`);
  console.log();

  // ─── 2. 分块质量 ───
  const perDoc = await pool.query(`
    SELECT d.source_path,
           count(c.id)::int AS chunks,
           round(avg(length(c.content)))::int AS avg_len,
           min(length(c.content))::int AS min_len,
           max(length(c.content))::int AS max_len,
           sum(CASE WHEN length(c.content) < 50 THEN 1 ELSE 0 END)::int AS tiny_chunks
    FROM documents d
    LEFT JOIN chunks c ON c.doc_id = d.id
    GROUP BY d.source_path
    ORDER BY chunks DESC
  `);
  console.log("【2. 分块质量（按文档）】");
  console.log("  文档 | 块数 | 平均长度 | 最短 | 最长 | 碎片块(<50字)");
  console.log("  " + "-".repeat(80));
  for (const row of perDoc.rows) {
    const name = path.basename(row.source_path);
    console.log(`  ${name.padEnd(30)} | ${String(row.chunks).padStart(3)} | ${String(row.avg_len).padStart(6)} | ${String(row.min_len).padStart(4)} | ${String(row.max_len).padStart(5)} | ${row.tiny_chunks}`);
  }
  // 全局分块长度分布
  const dist = await pool.query(`
    SELECT
      sum(CASE WHEN length(content) < 100 THEN 1 ELSE 0 END)::int AS under_100,
      sum(CASE WHEN length(content) >= 100 AND length(content) < 400 THEN 1 ELSE 0 END)::int AS r100_400,
      sum(CASE WHEN length(content) >= 400 AND length(content) < 800 THEN 1 ELSE 0 END)::int AS r400_800,
      sum(CASE WHEN length(content) >= 800 AND length(content) < 1200 THEN 1 ELSE 0 END)::int AS r800_1200,
      sum(CASE WHEN length(content) >= 1200 THEN 1 ELSE 0 END)::int AS over_1200,
      count(*)::int AS total
    FROM chunks
  `);
  const d = dist.rows[0];
  console.log(`\n  长度分布: <100=${d.under_100} | 100-400=${d.r100_400} | 400-800=${d.r400_800} | 800-1200=${d.r800_1200} | >1200=${d.over_1200} (总${d.total})`);
  console.log();

  // ─── 3. BM25 检索命中率 ───
  const qaSet: QaItem[] = JSON.parse(fs.readFileSync(QA_PATH, "utf8"));
  let bm25Hit = 0;
  const bm25Details: string[] = [];
  for (const qa of qaSet) {
    // 先用 jieba 分词（与 search.ts 一致）
    const tokRes = await pool.query("SELECT $1::pdb.jieba::text[] AS tokens", [qa.query]);
    const tokens = [...new Set(((tokRes.rows[0]?.tokens ?? []) as string[]).filter((t) => /[\p{L}\p{N}]/u.test(t)))];
    if (tokens.length === 0) {
      bm25Details.push(`  #${String(qa.id).padStart(2)} MISS | (分词为空) ${qa.query.slice(0, 30)}`);
      continue;
    }
    const result = await pool.query(
      `SELECT d.source_path, pdb.score(c.id) AS raw_score
       FROM chunks c JOIN documents d ON d.id = c.doc_id
       WHERE (c.content ||| $1::text[] OR c.heading ||| $1::text[])
       ORDER BY raw_score DESC
       LIMIT $2`,
      [tokens, TOP_K],
    ).catch(() => ({ rows: [] }));
    const hit = result.rows.some((r: { source_path: string }) =>
      r.source_path.includes(qa.expected_source.replace(/\.\w+$/, "")),
    );
    if (hit) bm25Hit++;
    bm25Details.push(`  #${String(qa.id).padStart(2)} ${hit ? "HIT" : "MISS"} | ${qa.query.slice(0, 30)}`);
  }
  console.log(`【3. BM25 检索命中率 (top-${TOP_K})】`);
  console.log(`  命中: ${bm25Hit}/${qaSet.length} = ${(bm25Hit / qaSet.length * 100).toFixed(1)}%`);
  for (const line of bm25Details) console.log(line);
  console.log();

  // ─── 4. 向量检索命中率 ───
  let vecHit = 0;
  const vecDetails: string[] = [];
  // 批量 embed 所有 query
  const queries = qaSet.map((q) => q.query);
  const queryEmbeddings = await embed(config, queries);
  for (let i = 0; i < qaSet.length; i++) {
    const qa = qaSet[i];
    const vec = toVectorLiteral(queryEmbeddings[i]);
    const result = await pool.query(`
      SELECT d.source_path, 1 - (chunks.embedding <=> '${vec}'::vector) AS similarity
      FROM chunks
      JOIN documents d ON d.id = chunks.doc_id
      WHERE chunks.embedding IS NOT NULL
      ORDER BY chunks.embedding <=> '${vec}'::vector
      LIMIT ${TOP_K}
    `);
    const hit = result.rows.some((r: { source_path: string }) =>
      r.source_path.includes(qa.expected_source.replace(/\.\w+$/, "")),
    );
    if (hit) vecHit++;
    const topSim = result.rows[0] ? Number(result.rows[0].similarity).toFixed(3) : "N/A";
    vecDetails.push(`  #${String(qa.id).padStart(2)} ${hit ? "HIT" : "MISS"} | sim=${topSim} | ${qa.query.slice(0, 30)}`);
  }
  console.log(`【4. 向量检索命中率 (top-${TOP_K})】`);
  console.log(`  命中: ${vecHit}/${qaSet.length} = ${(vecHit / qaSet.length * 100).toFixed(1)}%`);
  for (const line of vecDetails) console.log(line);
  console.log();

  // ─── 5. 图谱任务状态 ───
  const graphStatus = await pool.query(`
    SELECT status, count(*)::int AS n FROM graph_jobs GROUP BY status
  `);
  console.log("【5. 图谱抽取任务】");
  if (graphStatus.rows.length === 0) {
    console.log("  无任务记录");
  } else {
    for (const row of graphStatus.rows) {
      console.log(`  ${row.status}: ${row.n}`);
    }
  }
  console.log();

  // ─── 6. 近似重复块检测（cosine > 0.95 的 chunk 对） ───
  const dupResult = await pool.query(`
    SELECT a.id AS id_a, b.id AS id_b,
           da.source_path AS path_a, db.source_path AS path_b,
           1 - (a.embedding <=> b.embedding) AS sim
    FROM chunks a
    JOIN chunks b ON a.id < b.id
    JOIN documents da ON da.id = a.doc_id
    JOIN documents db ON db.id = b.doc_id
    WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL
      AND 1 - (a.embedding <=> b.embedding) > 0.95
    ORDER BY sim DESC
    LIMIT 20
  `);
  console.log("【6. 近似重复块检测（cosine > 0.95）】");
  if (dupResult.rows.length === 0) {
    console.log("  无近似重复块");
  } else {
    console.log(`  发现 ${dupResult.rows.length} 对:`);
    for (const row of dupResult.rows) {
      console.log(`  sim=${Number(row.sim).toFixed(4)} | chunk#${row.id_a}(${path.basename(row.path_a)}) <-> chunk#${row.id_b}(${path.basename(row.path_b)})`);
    }
  }
  console.log();

  // ─── 7. BM25 分词命中分析（对 MISS 的 query 诊断原因） ───
  console.log("【7. BM25 分词命中分析（MISS 诊断）】");
  let missCount = 0;
  for (const qa of qaSet) {
    // 检查该 query 在 BM25 top-5 是否 MISS
    const tokRes = await pool.query("SELECT $1::pdb.jieba::text[] AS tokens", [qa.query]);
    const tokens = [...new Set(((tokRes.rows[0]?.tokens ?? []) as string[]).filter((t) => /[\p{L}\p{N}]/u.test(t)))];
    if (tokens.length === 0) continue;
    const bm25Res = await pool.query(
      `SELECT d.source_path, pdb.score(c.id) AS raw_score
       FROM chunks c JOIN documents d ON d.id = c.doc_id
       WHERE (c.content ||| $1::text[] OR c.heading ||| $1::text[])
       ORDER BY raw_score DESC LIMIT 5`,
      [tokens, 5],
    ).catch(() => ({ rows: [] }));
    const stem = qa.expected_source.replace(/\.\w+$/, "");
    const hit = bm25Res.rows.some((r: { source_path: string }) => r.source_path.includes(stem));
    if (hit) continue;
    missCount++;
    // 诊断：目标文档的 chunks 里包含哪些 query token
    const targetChunks = await pool.query(
      `SELECT c.content FROM chunks c JOIN documents d ON d.id = c.doc_id WHERE d.source_path LIKE $1`,
      [`%${stem}%`],
    );
    const targetText = targetChunks.rows.map((r: { content: string }) => r.content).join(" ");
    const foundTokens = tokens.filter((t) => targetText.includes(t));
    const missingTokens = tokens.filter((t) => !targetText.includes(t));
    console.log(`  #${qa.id} "${qa.query.slice(0, 30)}"`);
    console.log(`    分词: [${tokens.join(", ")}]`);
    console.log(`    目标文档含: [${foundTokens.join(", ")}]`);
    console.log(`    目标文档缺: [${missingTokens.join(", ")}]`);
  }
  if (missCount === 0) console.log("  无 MISS，无需诊断");
  console.log();

  await pool.end();
  console.log("=== 完成 ===");
}

main().catch((err) => {
  console.error("指标脚本执行失败:", err);
  process.exit(1);
});
