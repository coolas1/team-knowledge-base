// 独立冒烟测试：node --experimental-strip-types scripts/smoke.ts
import { loadConfig } from "../src/config.ts";
import { checkDatabase, closePool, getPool } from "../src/core/db.ts";
import { checkGraph, closeDriver, ensureGraphSchema } from "../src/core/graph.ts";
import { embedOne } from "../src/core/embedding.ts";

const config = loadConfig();
console.log("vault:", config.vaultDir);

const dbProblems = await checkDatabase(config);
console.log("ParadeDB:", dbProblems.length === 0 ? "OK" : dbProblems.join("; "));

const graphProblems = await checkGraph(config);
if (graphProblems.length === 0) {
  await ensureGraphSchema(config);
  console.log("Neo4j: OK (constraints ensured)");
} else {
  console.log("Neo4j:", graphProblems.join("; "));
}

if (config.ark.apiKey) {
  const embedding = await embedOne(config, "冒烟测试：中文向量化");
  const norm = Math.sqrt(embedding.reduce((sum, v) => sum + v * v, 0));
  console.log(`ARK embedding: OK dim=${embedding.length} norm=${norm.toFixed(4)}`);
  const result = await getPool(config).query("SELECT 1 AS ok");
  console.log("PG query:", result.rows[0].ok === 1 ? "OK" : "FAIL");
} else {
  console.log("ARK embedding: 跳过（未设置 ARK_API_KEY）");
}

await Promise.allSettled([closePool(), closeDriver()]);
