import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerKnowledgeCommands } from "./adapters/pi/commands.ts";
import { registerKnowledgeLifecycle } from "./adapters/pi/lifecycle.ts";
import { registerKnowledgeSubagent } from "./adapters/pi/subagent.ts";
import { registerKnowledgeTools } from "./adapters/pi/tools.ts";
import { closePool } from "./core/db.ts";
import { closeDriver } from "./core/graph.ts";

/**
 * pi-knowledge：agentic 知识与记忆管理扩展。
 *
 * - 事实源：vault 目录（文档 + 记忆 + 维护报告）
 * - 派生索引：ParadeDB（BM25 + pgvector）+ Neo4j（知识图谱）
 * - 检索：细粒度工具 + knowledge_search 子代理
 * - 记忆：语义层（vault md）+ 情景层（session 档案），开场压缩视图注入
 */
export default function (pi: ExtensionAPI) {
  registerKnowledgeCommands(pi);
  registerKnowledgeTools(pi);
  registerKnowledgeSubagent(pi);
  registerKnowledgeLifecycle(pi); // 含 session_shutdown 反思，须先于下面的连接池关闭注册

  pi.on("session_shutdown", async () => {
    await Promise.allSettled([closePool(), closeDriver()]);
  });
}
