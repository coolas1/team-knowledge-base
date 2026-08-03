export interface TkbAdapterConfig {
  mcpUrl: string;
  connectTimeoutMs: number;
  defaultToolTimeoutMs: number;
  deepToolTimeoutMs: number;
  strictContract: boolean;
  enableLegacySearch: boolean;
  enableWriteTools: boolean;
  enableFullGraph: boolean;
}

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function enabled(value: string | undefined, fallback = false): boolean {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

export function loadTkbAdapterConfig(
  env: NodeJS.ProcessEnv = process.env,
): TkbAdapterConfig {
  return {
    mcpUrl: env.TKB_MCP_URL?.trim() || "http://localhost:8000/mcp/",
    connectTimeoutMs: positiveInteger(env.TKB_CONNECT_TIMEOUT_MS, 10_000),
    defaultToolTimeoutMs: positiveInteger(env.TKB_TOOL_TIMEOUT_MS, 60_000),
    deepToolTimeoutMs: positiveInteger(env.TKB_DEEP_TOOL_TIMEOUT_MS, 300_000),
    strictContract: enabled(env.TKB_CONTRACT_STRICT, true),
    enableLegacySearch: enabled(env.TKB_ENABLE_LEGACY_SEARCH),
    enableWriteTools: enabled(env.TKB_ENABLE_WRITE_TOOLS),
    enableFullGraph: enabled(env.TKB_ENABLE_FULL_GRAPH),
  };
}
