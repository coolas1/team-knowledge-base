/** 单条 query 调试：查看检索返回了什么 */
import { loadConfig } from "../src/config.ts";
import { hybridSearch } from "../src/core/search.ts";
import { closePool } from "../src/core/db.ts";

const query = process.argv[2] || "技术架构部6月份一共有多少人？迟到最多的是谁？";
const config = loadConfig();
const hits = await hybridSearch(config, query, { limit: 8 });

console.log(`query: ${query}\n`);
for (let i = 0; i < hits.length; i++) {
  const h = hits[i];
  console.log(`#${i + 1} [${h.signals.join("+")}] ${h.sourcePath}`);
  console.log(h.content);
  console.log();
}
await closePool();
