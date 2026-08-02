import fs from "node:fs/promises";
import path from "node:path";
import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { derivedDir, loadConfig } from "../../config.ts";
import { getPool } from "../../core/db.ts";
import { exploreDoc, exploreEntity } from "../../core/graph-explore.ts";
import { MEMORY_TYPES, type MemoryType, saveMemory } from "../../core/memory.ts";
import { type SearchHit, hybridSearch } from "../../core/search.ts";
import { sessionDerivedFrom } from "../../core/session-ingest.ts";
import { markMemoryViewDirty } from "./lifecycle.ts";

/**
 * A 层细粒度工具：hybrid_search / graph_expand / read_doc / memory_save。
 * 主 agent 直接注册；前三个同时复用为 knowledge_search 子代理的工具集。
 */

const KINDS = ["doc", "memory", "session"];

function formatHits(hits: SearchHit[]): string {
  if (hits.length === 0) return "无命中。";
  const lines: string[] = [];
  for (let i = 0; i < hits.length; i++) {
    const hit = hits[i];
    const via = hit.viaEntities ? ` via ${hit.viaEntities.join("/")}` : "";
    lines.push(
      `[${i + 1}] ${hit.sourcePath} (${hit.kind}, ${hit.signals.join("+")}${via})` +
        `${hit.heading ? ` § ${hit.heading}` : ""}`,
      hit.content,
      "",
    );
  }
  lines.push("提示: 用 knowledge_read_doc 按 source_path 读全文，用 knowledge_graph_expand 沿实体/文档探索关联。");
  return lines.join("\n");
}

function textResult(text: string, isError = false) {
  return { content: [{ type: "text" as const, text }], details: undefined as unknown, isError };
}

const SEARCH_PARAMS = Type.Object({
  query: Type.String({ description: "检索问题或关键词（中文自然语句即可）" }),
  limit: Type.Optional(Type.Number({ description: "返回条数，默认 8" })),
  kinds: Type.Optional(
    Type.Array(Type.String({ enum: KINDS }), {
      description: "限定类别：doc=文档, memory=提炼记忆, session=历史会话；缺省不限",
    }),
  ),
});

const GRAPH_PARAMS = Type.Object({
  entity: Type.Optional(Type.String({ description: "要探索的实体名（与 doc_path 二选一）" })),
  doc_path: Type.Optional(Type.String({ description: "要探索的文档 source_path（与 entity 二选一）" })),
});

const READ_DOC_PARAMS = Type.Object({
  source_path: Type.String({ description: "检索结果中的 source_path" }),
});

const MEMORY_SAVE_PARAMS = Type.Object({
  type: Type.String({ enum: [...MEMORY_TYPES], description: "记忆类别" }),
  title: Type.String({ description: "简短标题（作为文件名与去重依据）" }),
  content: Type.String({ description: "记忆内容：1-4 句自包含的陈述" }),
  priority: Type.Optional(
    Type.String({ enum: ["high", "normal"], description: "high 记忆每次会话开场注入全文，慎用" }),
  ),
});

/** 检索工具（子代理复用这组，不含 memory_save） */
export function searchToolDefinitions(): ToolDefinition[] {
  const hybridTool: ToolDefinition<typeof SEARCH_PARAMS> = {
    name: "knowledge_hybrid_search",
    label: "Knowledge Search",
    description:
      "在个人知识库（文档/记忆/历史会话）中三路融合检索（BM25+向量+图谱扩展），返回相关片段及来源路径。",
    promptSnippet: "knowledge_hybrid_search: 检索个人知识库（文档/记忆/历史会话）",
    parameters: SEARCH_PARAMS,
    async execute(_toolCallId, params) {
      const config = loadConfig();
      const hits = await hybridSearch(config, params.query, {
        limit: params.limit,
        kinds: params.kinds,
      });
      return textResult(formatHits(hits));
    },
  };

  const graphTool: ToolDefinition<typeof GRAPH_PARAMS> = {
    name: "knowledge_graph_expand",
    label: "Knowledge Graph",
    description:
      "沿知识图谱探索：给定实体名看它的关联文档与实体关系，或给定文档路径看它提及的实体、相关文档与溯源。",
    promptSnippet: "knowledge_graph_expand: 沿知识图谱探索实体/文档的关联",
    parameters: GRAPH_PARAMS,
    async execute(_toolCallId, params) {
      const config = loadConfig();
      if (params.entity) return textResult(await exploreEntity(config, params.entity));
      if (params.doc_path) return textResult(await exploreDoc(config, params.doc_path));
      return textResult("entity 与 doc_path 必须提供其一。", true);
    },
  };

  const readDocTool: ToolDefinition<typeof READ_DOC_PARAMS> = {
    name: "knowledge_read_doc",
    label: "Knowledge Read",
    description: "按 source_path 读取知识库文档的完整内容（影子 Markdown 全文）。",
    parameters: READ_DOC_PARAMS,
    async execute(_toolCallId, params) {
      const config = loadConfig();
      const result = await getPool(config).query(
        "SELECT shadow_path FROM documents WHERE source_path = $1",
        [params.source_path],
      );
      if (result.rows.length === 0) return textResult(`文档不存在: ${params.source_path}`, true);
      const shadowPath: string = result.rows[0].shadow_path;
      if (shadowPath !== "") {
        const text = await fs.readFile(path.join(derivedDir(config), shadowPath), "utf8");
        return textResult(text);
      }
      // session 档案无影子文件，从 chunks 拼接
      const chunks = await getPool(config).query(
        `SELECT c.content FROM chunks c JOIN documents d ON d.id = c.doc_id
         WHERE d.source_path = $1 ORDER BY c.seq`,
        [params.source_path],
      );
      return textResult(chunks.rows.map((row) => row.content).join("\n\n"));
    },
  };

  return [hybridTool as ToolDefinition, graphTool as ToolDefinition, readDocTool as ToolDefinition];
}

function memorySaveDefinition(): ToolDefinition {
  const tool: ToolDefinition<typeof MEMORY_SAVE_PARAMS> = {
    name: "memory_save",
    label: "Memory Save",
    description:
      "保存一条长期记忆（用户偏好/事实/经验/决策）到知识库。当用户明确要求记住某事，或出现明显值得跨会话保留的信息时使用。",
    promptSnippet: "memory_save: 保存长期记忆（偏好/事实/经验/决策）",
    parameters: MEMORY_SAVE_PARAMS,
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const config = loadConfig();
      const sessionFile = ctx.sessionManager.getSessionFile();
      const relPath = await saveMemory(config, {
        type: params.type as MemoryType,
        title: params.title,
        content: params.content,
        priority: params.priority === "high" ? "high" : "normal",
        derivedFrom: sessionFile ? sessionDerivedFrom(sessionFile) : undefined,
      });
      markMemoryViewDirty();
      return textResult(`已保存记忆: ${relPath}`);
    },
  };
  return tool as ToolDefinition;
}

/** 注册 A 层工具到主 agent */
export function registerKnowledgeTools(pi: ExtensionAPI): void {
  for (const tool of [...searchToolDefinitions(), memorySaveDefinition()]) {
    pi.registerTool(tool);
  }
}
