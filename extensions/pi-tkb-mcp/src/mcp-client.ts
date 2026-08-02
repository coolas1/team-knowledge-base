import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { loadTkbConfig } from "./config.ts";

/**
 * TKB MCP 客户端封装：管理与 team-knowledge-base MCP 服务端的连接。
 * 采用短连接模式——每次工具调用建立连接、执行、关闭，避免长连接维护开销。
 */

export interface McpToolInfo {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

let discoveredTools: McpToolInfo[] | null = null;

/** 连接 MCP 服务端并发现可用工具（缓存结果） */
export async function discoverTools(): Promise<McpToolInfo[]> {
  if (discoveredTools) return discoveredTools;

  const config = loadTkbConfig();
  const client = new Client({ name: "pi-tkb-mcp", version: "0.1.0" });
  const transport = new StreamableHTTPClientTransport(new URL(config.mcpUrl));

  try {
    await client.connect(transport);
    const result = await client.listTools();
    discoveredTools = result.tools.map((t) => ({
      name: t.name,
      description: t.description ?? "",
      inputSchema: (t.inputSchema ?? {}) as Record<string, unknown>,
    }));
    return discoveredTools;
  } finally {
    await client.close();
  }
}

/** 调用 MCP 工具（短连接） */
export async function callMcpTool(
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const config = loadTkbConfig();
  const client = new Client({ name: "pi-tkb-mcp", version: "0.1.0" });
  const transport = new StreamableHTTPClientTransport(new URL(config.mcpUrl));

  try {
    await client.connect(transport);
    const result = await client.callTool({ name: toolName, arguments: args });
    // MCP tool result content 是 TextContent[] | ImageContent[] 等
    const parts = result.content as Array<{ type: string; text?: string }>;
    return parts
      .filter((p) => p.type === "text" && p.text)
      .map((p) => p.text)
      .join("\n");
  } finally {
    await client.close();
  }
}

/** 清除工具缓存（重连时调用） */
export function resetDiscovery(): void {
  discoveredTools = null;
}
