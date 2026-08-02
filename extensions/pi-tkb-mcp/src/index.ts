import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { registerTkbSubagent } from "./subagent.ts";
import { registerTkbTools } from "./tools.ts";

/**
 * pi-tkb-mcp：通过 MCP 协议接入 team-knowledge-base 的 agentic RAG 扩展。
 *
 * 架构：
 *   pi agent ←(extension tools)→ MCP client ←(streamable HTTP)→ TKB MCP server
 *   TKB MCP server → GraphRAG engine（Postgres+pgvector / Neo4j / Ollama）
 *
 * 注册内容：
 *   - A 层工具：tkb_search / tkb_get_document / tkb_query_graph /
 *               tkb_upload_document / tkb_list_documents / tkb_remove_document /
 *               tkb_get_full_graph
 *   - B 层子代理：tkb_research（多轮 GraphRAG 检索，隔离上下文）
 */
export default function (pi: ExtensionAPI) {
  registerTkbTools(pi);
  registerTkbSubagent(pi);
}
