/**
 * 建立 entity_vectors / relation_vectors 表 + 重建图谱（含实体向量）
 * 用法: node --experimental-strip-types infra/rebuild-graph.ts
 */
import { loadConfig } from "../src/config.ts";
import { getPool } from "../src/core/db.ts";
import { processGraphJobs } from "../src/core/graph-build.ts";
import { ensureGraphSchema } from "../src/core/graph.ts";

const config = loadConfig();
const pool = getPool(config);

// 1. 确保新表存在
await pool.query(`
  CREATE TABLE IF NOT EXISTS entity_vectors (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    type        text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    embedding   vector(1024)
  )`);
await pool.query(`
  CREATE INDEX IF NOT EXISTS entity_vectors_embedding_idx ON entity_vectors
  USING hnsw (embedding vector_cosine_ops)`);
await pool.query(`
  CREATE TABLE IF NOT EXISTS relation_vectors (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    head        text NOT NULL,
    rel_type    text NOT NULL,
    tail        text NOT NULL,
    description text NOT NULL DEFAULT '',
    embedding   vector(1024),
    UNIQUE (head, rel_type, tail)
  )`);
await pool.query(`
  CREATE INDEX IF NOT EXISTS relation_vectors_embedding_idx ON relation_vectors
  USING hnsw (embedding vector_cosine_ops)`);
console.log("表已就绪: entity_vectors, relation_vectors");

// 2. 清空旧向量数据（重建时重新生成）
await pool.query("DELETE FROM entity_vectors");
await pool.query("DELETE FROM relation_vectors");

// 3. 确保 Neo4j Community 约束
await ensureGraphSchema(config);

// 4. 重置图谱任务
await pool.query("UPDATE graph_jobs SET status = 'pending', attempts = 0");
const pending = await pool.query("SELECT count(*) FROM graph_jobs WHERE status = 'pending'");
console.log(`pending jobs: ${pending.rows[0].count}`);

// 5. 消费图谱任务（新 prompt 含 description + 实体向量写入）
console.log("开始重建图谱（含实体/关系向量）...");
const stats = await processGraphJobs(config, {
  limit: 50,
  onProgress: (msg) => console.log(`  ${msg}`),
});
console.log(`\n完成: 处理 ${stats.processed}, 成功 ${stats.done}, 失败 ${stats.failed.length}`);
if (stats.failed.length > 0) {
  for (const f of stats.failed) console.log(`  FAIL: ${f.path}: ${f.error}`);
}

// 6. 统计向量数据
const ec = await pool.query("SELECT count(*) FROM entity_vectors");
const rc = await pool.query("SELECT count(*) FROM relation_vectors");
console.log(`\n实体向量: ${ec.rows[0].count} 条, 关系向量: ${rc.rows[0].count} 条`);
process.exit(0);
