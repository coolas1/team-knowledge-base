import type { ExtensionAPI, ToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { callMcpTool } from "./mcp-client.ts";

/**
 * 将 TKB MCP 服务端工具注册为 pi agent 工具。
 * 采用静态映射（对应 mcp.py 中注册的 7 个工具），保证 schema 精确。
 */

function textResult(text: string, isError = false) {
  return { content: [{ type: "text" as const, text }], details: undefined as unknown, isError };
}

const SEARCH_PARAMS = Type.Object({
  query: Type.String({ description: "检索问题或关键词" }),
});

const GET_DOC_PARAMS = Type.Object({
  doc_id: Type.String({ description: "文档 UUID" }),
});

const QUERY_GRAPH_PARAMS = Type.Object({
  entity_name: Type.String({ description: "实体名称" }),
  include_neighbors: Type.Optional(
    Type.Boolean({ description: "是否包含邻居节点，默认 true" }),
  ),
  hops: Type.Optional(Type.Number({ description: "邻居跳数，默认 2" })),
});

const UPLOAD_PARAMS = Type.Object({
  file_name: Type.String({ description: "文件名（含扩展名，如 report.pdf）" }),
  content: Type.String({ description: "文档文本内容" }),
});

const LIST_PARAMS = Type.Object({
  page: Type.Optional(Type.Number({ description: "页码，默认 1" })),
  page_size: Type.Optional(Type.Number({ description: "每页条数，默认 20" })),
  file_type: Type.Optional(Type.String({ description: "按文件类型筛选（pdf/docx/pptx/md）" })),
  status: Type.Optional(Type.String({ description: "按状态筛选（indexed/processing/failed）" })),
});

const REMOVE_PARAMS = Type.Object({
  doc_id: Type.String({ description: "要删除的文档 UUID" }),
});

const FULL_GRAPH_PARAMS = Type.Object({});

/** 静态工具定义（与 team-knowledge-base/src/engine/mcp.py 一一对应） */
function buildToolDefinitions(): ToolDefinition[] {
  const searchTool: ToolDefinition<typeof SEARCH_PARAMS> = {
    name: "tkb_search",
    label: "TKB Search",
    description:
      "在团队知识库中语义检索（向量粗筛 -> Reranker 守门 -> 图谱增强），返回相关文本片段、实体与文档。",
    promptSnippet: "tkb_search: 检索团队知识库（GraphRAG 语义搜索）",
    parameters: SEARCH_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("search", { query: params.query });
        return textResult(raw);
      } catch (e) {
        return textResult(`TKB 检索失败: ${e}`, true);
      }
    },
  };

  const getDocTool: ToolDefinition<typeof GET_DOC_PARAMS> = {
    name: "tkb_get_document",
    label: "TKB Document",
    description: "获取团队知识库中某文档的详情（全文、overview、状态等）。",
    promptSnippet: "tkb_get_document: 按 ID 获取知识库文档详情",
    parameters: GET_DOC_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("get_document", { doc_id: params.doc_id });
        return textResult(raw);
      } catch (e) {
        return textResult(`获取文档失败: ${e}`, true);
      }
    },
  };

  const queryGraphTool: ToolDefinition<typeof QUERY_GRAPH_PARAMS> = {
    name: "tkb_query_graph",
    label: "TKB Graph",
    description:
      "查询团队知识库知识图谱中的实体及其关系、邻居节点。",
    promptSnippet: "tkb_query_graph: 查询知识图谱实体与关系",
    parameters: QUERY_GRAPH_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("query_graph", {
          entity_name: params.entity_name,
          include_neighbors: params.include_neighbors ?? true,
          hops: params.hops ?? 2,
        });
        return textResult(raw);
      } catch (e) {
        return textResult(`图谱查询失败: ${e}`, true);
      }
    },
  };

  const uploadTool: ToolDefinition<typeof UPLOAD_PARAMS> = {
    name: "tkb_upload_document",
    label: "TKB Upload",
    description: "上传文本文档到团队知识库（自动提取、分块、建图）。",
    promptSnippet: "tkb_upload_document: 上传文档到团队知识库",
    parameters: UPLOAD_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("upload_document", {
          file_name: params.file_name,
          content: params.content,
        });
        return textResult(raw);
      } catch (e) {
        return textResult(`上传失败: ${e}`, true);
      }
    },
  };

  const listTool: ToolDefinition<typeof LIST_PARAMS> = {
    name: "tkb_list_documents",
    label: "TKB List",
    description: "列出团队知识库中的文档（分页，可按类型/状态筛选）。",
    promptSnippet: "tkb_list_documents: 列出知识库文档",
    parameters: LIST_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("list_documents", {
          page: params.page ?? 1,
          page_size: params.page_size ?? 20,
          file_type: params.file_type ?? null,
          status: params.status ?? null,
        });
        return textResult(raw);
      } catch (e) {
        return textResult(`列表查询失败: ${e}`, true);
      }
    },
  };

  const removeTool: ToolDefinition<typeof REMOVE_PARAMS> = {
    name: "tkb_remove_document",
    label: "TKB Remove",
    description: "从团队知识库中删除文档（级联删除 chunks、图谱、文件）。",
    promptSnippet: "tkb_remove_document: 删除知识库文档",
    parameters: REMOVE_PARAMS,
    async execute(_toolCallId, params) {
      try {
        const raw = await callMcpTool("remove_document", { doc_id: params.doc_id });
        return textResult(raw);
      } catch (e) {
        return textResult(`删除失败: ${e}`, true);
      }
    },
  };

  const fullGraphTool: ToolDefinition<typeof FULL_GRAPH_PARAMS> = {
    name: "tkb_get_full_graph",
    label: "TKB Full Graph",
    description: "获取团队知识库的完整知识图谱（所有实体与关系）。",
    promptSnippet: "tkb_get_full_graph: 获取完整知识图谱",
    parameters: FULL_GRAPH_PARAMS,
    async execute() {
      try {
        const raw = await callMcpTool("get_full_graph", {});
        return textResult(raw);
      } catch (e) {
        return textResult(`获取全图失败: ${e}`, true);
      }
    },
  };

  return [
    searchTool as ToolDefinition,
    getDocTool as ToolDefinition,
    queryGraphTool as ToolDefinition,
    uploadTool as ToolDefinition,
    listTool as ToolDefinition,
    removeTool as ToolDefinition,
    fullGraphTool as ToolDefinition,
  ];
}

/** 供子代理复用的工具集（不含 upload/remove 写操作） */
export function searchToolDefinitions(): ToolDefinition[] {
  const all = buildToolDefinitions();
  return all.filter(
    (t) => !["tkb_upload_document", "tkb_remove_document"].includes(t.name),
  );
}

/** 注册全部 TKB 工具到主 agent */
export function registerTkbTools(pi: ExtensionAPI): void {
  for (const tool of buildToolDefinitions()) {
    pi.registerTool(tool);
  }
}
