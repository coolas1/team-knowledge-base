import fs from "node:fs";
import path from "node:path";

/**
 * pi-tkb-mcp 配置：从环境变量或 knowledge.json 读取 TKB MCP 服务端地址。
 */

export interface TkbConfig {
  /** TKB MCP 服务端 URL（streamable HTTP 端点） */
  mcpUrl: string;
  /** 连接超时 ms */
  connectTimeoutMs: number;
}

const DEFAULT_MCP_URL = "http://localhost:8000/mcp";
const DEFAULT_TIMEOUT = 10_000;

let cached: TkbConfig | null = null;

export function loadTkbConfig(): TkbConfig {
  if (cached) return cached;

  let mcpUrl = process.env.TKB_MCP_URL ?? "";

  // 尝试从 knowledge.json 读取（与 pi-knowledge 共享配置位置）
  if (!mcpUrl) {
    const configPaths = [
      path.join(process.env.HOME ?? process.env.USERPROFILE ?? "", ".pi", "knowledge.json"),
      path.join(process.cwd(), ".pi", "knowledge.json"),
    ];
    for (const p of configPaths) {
      try {
        if (fs.existsSync(p)) {
          const data = JSON.parse(fs.readFileSync(p, "utf8"));
          if (data.tkb_mcp_url) {
            mcpUrl = data.tkb_mcp_url;
            break;
          }
        }
      } catch {
        // ignore malformed config
      }
    }
  }

  cached = {
    mcpUrl: mcpUrl || DEFAULT_MCP_URL,
    connectTimeoutMs: Number(process.env.TKB_CONNECT_TIMEOUT_MS) || DEFAULT_TIMEOUT,
  };
  return cached;
}
