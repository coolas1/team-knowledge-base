import {
  defineTool,
  type ExtensionAPI,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { TkbAdapterConfig } from "./config.js";
import { loadTkbAdapterConfig } from "./config.js";
import { TkbMcpClient } from "./mcp-client.js";
import { redact } from "./runner-client.js";

type PiToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details: { mcpTool: string; arguments: Record<string, unknown> };
  isError: boolean;
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? `${error.name}: ${error.message}` : String(error);
}

async function executeMcpTool(
  client: TkbMcpClient,
  mcpTool: string,
  args: Record<string, unknown>,
  signal: AbortSignal | undefined,
  timeoutMs: number,
): Promise<PiToolResult> {
  try {
    const result = await client.callTool(mcpTool, args, { signal, timeoutMs });
    if (result.isError) throw new Error(result.text || "MCP returned a tool error");
    return {
      content: [{ type: "text", text: result.text || "TKB MCP returned no content." }],
      details: { mcpTool, arguments: args },
      isError: result.isError,
    };
  } catch (error) {
    throw new Error(redact(`TKB tool ${mcpTool} failed: ${errorMessage(error)}`));
  }
}

const SEARCH_PARAMS = Type.Object({
  query: Type.String({ description: "Knowledge-base question or search phrase" }),
  top_k: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
});

const QUERY_PARAMS = Type.Object({
  query: Type.String({ description: "Question for Hindsight recall or reflect" }),
  strategy: Type.Optional(
    Type.Union([Type.Literal("auto"), Type.Literal("recall"), Type.Literal("reflect")]),
  ),
  mode: Type.Optional(Type.Union([Type.Literal("fast"), Type.Literal("deep")])),
  top_k: Type.Optional(Type.Integer({ minimum: 1, maximum: 50 })),
  needs_answer: Type.Optional(Type.Boolean()),
});

const GET_DOCUMENT_PARAMS = Type.Object({
  doc_id: Type.String({ description: "Document UUID" }),
});

const QUERY_GRAPH_PARAMS = Type.Object({
  entity_name: Type.String({ description: "Entity name" }),
  include_neighbors: Type.Optional(Type.Boolean()),
  hops: Type.Optional(Type.Integer({ minimum: 1, maximum: 3 })),
});

const LIST_DOCUMENTS_PARAMS = Type.Object({
  page: Type.Optional(Type.Integer({ minimum: 1 })),
  page_size: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
  file_type: Type.Optional(Type.String()),
  status: Type.Optional(Type.String()),
});

const UPLOAD_PARAMS = Type.Object({
  file_name: Type.String(),
  content: Type.String({ description: "UTF-8 text content" }),
});

const REMOVE_PARAMS = Type.Object({ doc_id: Type.String() });
const GENERATE_DOCUMENT_PARAMS = Type.Object({
  format: Type.Union([
    Type.Literal("docx"),
    Type.Literal("pdf"),
    Type.Literal("pptx"),
  ], { description: "Output format: Word, PDF, or PowerPoint" }),
  title: Type.String({ minLength: 1, description: "Document title" }),
  content: Type.String({
    minLength: 1,
    description:
      "Markdown content. For pptx, separate slides with a line containing only --- and start each slide with a heading.",
  }),
  file_name: Type.Optional(Type.String({ description: "Optional output filename" })),
});
const EMPTY_PARAMS = Type.Object({});

export interface BuildToolsOptions {
  client?: TkbMcpClient;
  config?: TkbAdapterConfig;
}

export function buildAllTkbTools(options: BuildToolsOptions = {}): ToolDefinition[] {
  const config = options.config ?? loadTkbAdapterConfig();
  const client = options.client ?? new TkbMcpClient(config);
  const normal = config.defaultToolTimeoutMs;
  const deep = config.deepToolTimeoutMs;

  const tools = [
    defineTool({
      name: "tkb_search",
      label: "TKB Search (legacy)",
      description: "Compatibility search through the engine's legacy search entry point.",
      parameters: SEARCH_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(client, "search", { query: params.query, top_k: params.top_k ?? 20 }, signal, normal),
    }),
    defineTool({
      name: "tkb_query_knowledge",
      label: "TKB Hindsight Query",
      description:
        "Query Hindsight with recall or reflect. Use reflect for complex synthesis and recall for direct evidence retrieval.",
      parameters: QUERY_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "query_knowledge",
          {
            query: params.query,
            strategy: params.strategy ?? "auto",
            mode: params.mode ?? "deep",
            top_k: params.top_k ?? 10,
            needs_answer: params.needs_answer ?? false,
          },
          signal,
          deep,
        ),
    }),
    defineTool({
      name: "tkb_search_fast",
      label: "TKB Fast Search",
      description:
        "Fast evidence search for a simple fact, definition, explicit keyword, file content, or file location.",
      parameters: SEARCH_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "search_knowledge_fast",
          { query: params.query, top_k: params.top_k ?? 5 },
          signal,
          normal,
        ),
    }),
    defineTool({
      name: "tkb_search_deep",
      label: "TKB Deep Search",
      description:
        "Deep evidence search for cross-document comparison, multi-hop relationships, timelines, causes, and synthesis.",
      parameters: SEARCH_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "search_knowledge_deep",
          { query: params.query, top_k: params.top_k ?? 10 },
          signal,
          deep,
        ),
    }),
    defineTool({
      name: "tkb_get_document",
      label: "TKB Document",
      description: "Read a knowledge-base document by UUID after search identifies it.",
      parameters: GET_DOCUMENT_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(client, "get_document", { doc_id: params.doc_id }, signal, normal),
    }),
    defineTool({
      name: "tkb_query_graph",
      label: "TKB Graph",
      description: "Query one GraphRAG entity and optionally its nearby relationships.",
      parameters: QUERY_GRAPH_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "query_graph",
          {
            entity_name: params.entity_name,
            include_neighbors: params.include_neighbors ?? true,
            hops: params.hops ?? 2,
          },
          signal,
          normal,
        ),
    }),
    defineTool({
      name: "tkb_list_documents",
      label: "TKB Documents",
      description: "List indexed knowledge-base documents with pagination and filters.",
      parameters: LIST_DOCUMENTS_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "list_documents",
          {
            page: params.page ?? 1,
            page_size: params.page_size ?? 20,
            file_type: params.file_type ?? null,
            status: params.status ?? null,
          },
          signal,
          normal,
        ),
    }),
    defineTool({
      name: "tkb_generate_document",
      label: "Generate Document",
      description:
        "Generate a downloadable Word (.docx), PDF, or PowerPoint (.pptx). PowerPoint output also includes editable Slidev Markdown. Use after drafting complete content.",
      parameters: GENERATE_DOCUMENT_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "generate_document",
          {
            format: params.format,
            title: params.title,
            content: params.content,
            file_name: params.file_name ?? null,
          },
          signal,
          deep,
        ),
    }),
    defineTool({
      name: "tkb_upload_document",
      label: "TKB Upload",
      description: "Upload UTF-8 text to the knowledge base. Disabled by default.",
      parameters: UPLOAD_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(
          client,
          "upload_document",
          { file_name: params.file_name, content: params.content },
          signal,
          deep,
        ),
    }),
    defineTool({
      name: "tkb_remove_document",
      label: "TKB Remove",
      description: "Permanently remove one document. Disabled by default.",
      parameters: REMOVE_PARAMS,
      execute: (_id, params, signal) =>
        executeMcpTool(client, "remove_document", { doc_id: params.doc_id }, signal, normal),
    }),
    defineTool({
      name: "tkb_get_full_graph",
      label: "TKB Full Graph",
      description: "Return the complete GraphRAG graph. Disabled by default because output can be large.",
      parameters: EMPTY_PARAMS,
      execute: (_id, _params, signal) =>
        executeMcpTool(client, "get_full_graph", {}, signal, deep),
    }),
  ];

  return tools as ToolDefinition[];
}

export function enabledTkbTools(options: BuildToolsOptions = {}): ToolDefinition[] {
  const config = options.config ?? loadTkbAdapterConfig();
  return buildAllTkbTools({ ...options, config }).filter((tool) => {
    if (tool.name === "tkb_search") return config.enableLegacySearch;
    if (["tkb_upload_document", "tkb_remove_document"].includes(tool.name)) {
      return config.enableWriteTools;
    }
    if (tool.name === "tkb_get_full_graph") return config.enableFullGraph;
    return true;
  });
}

export function registerTkbTools(
  pi: ExtensionAPI,
  options: BuildToolsOptions = {},
): void {
  for (const tool of enabledTkbTools(options)) pi.registerTool(tool);
}
