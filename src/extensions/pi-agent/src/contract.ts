import type { McpToolInfo, TkbMcpClient } from "./mcp-client.js";

interface ToolContract {
  required: string[];
}

export const ENGINE_MCP_CONTRACT: Record<string, ToolContract> = {
  search: { required: ["query"] },
  query_knowledge: { required: ["query"] },
  search_knowledge_fast: { required: ["query"] },
  search_knowledge_deep: { required: ["query"] },
  get_document: { required: ["doc_id"] },
  query_graph: { required: ["entity_name"] },
  upload_document: { required: ["file_name", "content"] },
  list_documents: { required: [] },
  remove_document: { required: ["doc_id"] },
  get_full_graph: { required: [] },
  generate_document: { required: ["format", "title", "content"] },
};

export interface ContractReport {
  ok: boolean;
  missingTools: string[];
  schemaErrors: string[];
  availableTools: string[];
}

export function inspectEngineContract(tools: McpToolInfo[]): ContractReport {
  const byName = new Map(tools.map((tool) => [tool.name, tool]));
  const missingTools = Object.keys(ENGINE_MCP_CONTRACT).filter(
    (name) => !byName.has(name),
  );
  const schemaErrors: string[] = [];

  for (const [name, contract] of Object.entries(ENGINE_MCP_CONTRACT)) {
    const tool = byName.get(name);
    if (!tool) continue;
    const properties = (tool.inputSchema.properties ?? {}) as Record<string, unknown>;
    const required = new Set(
      Array.isArray(tool.inputSchema.required)
        ? tool.inputSchema.required.filter((value): value is string => typeof value === "string")
        : [],
    );
    for (const parameter of contract.required) {
      if (!(parameter in properties) || !required.has(parameter)) {
        schemaErrors.push(`${name}.${parameter} must be required`);
      }
    }
  }

  return {
    ok: missingTools.length === 0 && schemaErrors.length === 0,
    missingTools,
    schemaErrors,
    availableTools: tools.map((tool) => tool.name).sort(),
  };
}

export async function validateEngineContract(
  client: TkbMcpClient,
  signal?: AbortSignal,
): Promise<ContractReport> {
  return inspectEngineContract(await client.listTools(signal));
}

export function formatContractErrors(report: ContractReport): string {
  return [
    report.missingTools.length
      ? `missing tools: ${report.missingTools.join(", ")}`
      : "",
    report.schemaErrors.length
      ? `schema errors: ${report.schemaErrors.join(", ")}`
      : "",
  ]
    .filter(Boolean)
    .join("; ");
}
