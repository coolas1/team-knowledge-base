export interface TkbAdapterConfig {
  mcpUrl: string;
  connectTimeoutMs: number;
  defaultToolTimeoutMs: number;
  deepToolTimeoutMs: number;
  strictContract: boolean;
  enableLegacySearch: boolean;
  enableWriteTools: boolean;
  enableFullGraph: boolean;
  conversationMemoryEnabled: boolean;
  conversationMemoryRecallTimeoutMs: number;
  conversationMemoryRecallLimit: number;
  conversationMemoryContextBudgetChars: number;
  conversationMemoryRetentionContext: string;
}

export type PiModelApi =
  | "openai-completions"
  | "openai-responses"
  | "anthropic-messages";

export type PiThinkingLevel =
  | "off"
  | "minimal"
  | "low"
  | "medium"
  | "high"
  | "xhigh"
  | "max";

export interface PiAgentConfig {
  toolAuthoringEnabled: boolean;
  runnerUrl: string;
  runnerToken: string;
  toolLibraryDir: string;
  maxCodeJobs: number;
  maxBuildAttempts: number;
  host: string;
  port: number;
  dataDir: string;
  sessionDir: string;
  cwd: string;
  provider: string;
  model: string;
  modelName: string;
  modelApi: PiModelApi;
  modelBaseUrl: string;
  modelApiKey: string;
  modelReasoning: boolean;
  thinkingLevel: PiThinkingLevel;
  exposeThinking: boolean;
  exposeToolResults: boolean;
  contextWindow: number;
  maxOutputTokens: number;
  maxToolCalls: number;
  maxRunSeconds: number;
  maxLoadedSessions: number;
  maxRequestBytes: number;
}

function positiveInteger(value: string | undefined, fallback: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function requiredPositiveInteger(
  value: string | undefined,
  fallback: number,
  field: string,
  maximum?: number,
): number {
  if (value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  if (
    !Number.isInteger(parsed) ||
    parsed <= 0 ||
    (maximum !== undefined && parsed > maximum)
  ) {
    const bound = maximum === undefined ? "greater than zero" : `between 1 and ${maximum}`;
    throw new Error(`${field} must be ${bound}`);
  }
  return parsed;
}

function enabled(value: string | undefined, fallback = false): boolean {
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function enumValue<T extends string>(
  value: string | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
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
    conversationMemoryEnabled: enabled(env.TKB_CONVERSATION_MEMORY_ENABLED),
    conversationMemoryRecallTimeoutMs: requiredPositiveInteger(
      env.TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS,
      5_000,
      "TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS",
    ),
    conversationMemoryRecallLimit: requiredPositiveInteger(
      env.TKB_CONVERSATION_MEMORY_RECALL_LIMIT,
      5,
      "TKB_CONVERSATION_MEMORY_RECALL_LIMIT",
      20,
    ),
    conversationMemoryContextBudgetChars: requiredPositiveInteger(
      env.TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS,
      6_000,
      "TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS",
    ),
    conversationMemoryRetentionContext:
      env.TKB_CONVERSATION_MEMORY_RETENTION_CONTEXT?.trim() ||
      "Completed team conversation turn",
  };
}

export function loadPiAgentConfig(
  env: NodeJS.ProcessEnv = process.env,
): PiAgentConfig {
  const cwd = env.PI_AGENT_CWD?.trim() || process.cwd();
  const dataDir = env.PI_AGENT_DATA_DIR?.trim() || `${cwd}/.pi-agent-data`;
  const sharedProvider = env.LLM_PROVIDER?.trim();
  const inheritSharedModel =
    Boolean(sharedProvider) &&
    !["none", "todo", "disabled"].includes(sharedProvider!.toLowerCase());
  const provider =
    env.PI_AGENT_PROVIDER?.trim() ||
    (inheritSharedModel ? sharedProvider : undefined) ||
    "ollama";
  const isOllama = provider.toLowerCase() === "ollama";
  const model =
    env.PI_AGENT_MODEL?.trim() ||
    (inheritSharedModel ? env.LLM_MODEL?.trim() : undefined) ||
    "qwen3:14b";
  return {
    host: env.PI_AGENT_HOST?.trim() || "127.0.0.1",
    toolAuthoringEnabled: enabled(env.PI_AGENT_TOOL_AUTHORING_ENABLED, true),
    runnerUrl: env.PI_AGENT_RUNNER_URL?.trim() || "",
    runnerToken: env.PI_AGENT_RUNNER_TOKEN?.trim() || "",
    toolLibraryDir: env.PI_AGENT_TOOL_LIBRARY_DIR?.trim() || `${dataDir}/tool-library`,
    maxCodeJobs: requiredPositiveInteger(env.PI_AGENT_MAX_CODE_JOBS, 12, "PI_AGENT_MAX_CODE_JOBS", 100),
    maxBuildAttempts: requiredPositiveInteger(env.PI_AGENT_MAX_BUILD_ATTEMPTS, 3, "PI_AGENT_MAX_BUILD_ATTEMPTS", 10),
    port: positiveInteger(env.PI_AGENT_PORT, 8010),
    dataDir,
    sessionDir: env.PI_AGENT_SESSION_DIR?.trim() || `${dataDir}/sessions`,
    cwd,
    provider,
    model,
    modelName: env.PI_AGENT_MODEL_NAME?.trim() || model,
    modelApi: enumValue(
      env.PI_AGENT_API,
      ["openai-completions", "openai-responses", "anthropic-messages"] as const,
      "openai-completions",
    ),
    modelBaseUrl:
      env.PI_AGENT_BASE_URL?.trim() ||
      (inheritSharedModel ? env.LLM_BASE_URL?.trim() : undefined) ||
      "http://localhost:11434/v1",
    modelApiKey:
      env.PI_AGENT_API_KEY?.trim() ||
      (inheritSharedModel ? env.LLM_API_KEY?.trim() : undefined) ||
      (isOllama ? "ollama" : ""),
    modelReasoning: enabled(env.PI_AGENT_REASONING, true),
    thinkingLevel: enumValue(
      env.PI_AGENT_THINKING_LEVEL,
      ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const,
      "medium",
    ),
    exposeThinking: enabled(env.PI_AGENT_EXPOSE_THINKING),
    exposeToolResults: enabled(env.PI_AGENT_EXPOSE_TOOL_RESULTS),
    contextWindow: positiveInteger(env.PI_AGENT_CONTEXT_WINDOW, 32_768),
    maxOutputTokens: positiveInteger(env.PI_AGENT_MAX_OUTPUT_TOKENS, 8_192),
    maxToolCalls: positiveInteger(env.PI_AGENT_MAX_TOOL_CALLS, 12),
    maxRunSeconds: positiveInteger(env.PI_AGENT_MAX_RUN_SECONDS, 300),
    maxLoadedSessions: positiveInteger(env.PI_AGENT_MAX_LOADED_SESSIONS, 50),
    maxRequestBytes: positiveInteger(env.PI_AGENT_MAX_REQUEST_BYTES, 1_048_576),
  };
}
