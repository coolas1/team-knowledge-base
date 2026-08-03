import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { TkbAdapterConfig } from "./config.js";
import { loadTkbAdapterConfig } from "./config.js";

export interface McpToolInfo {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface McpCallResult {
  text: string;
  isError: boolean;
}

export interface McpClientLike {
  connect(transport: unknown): Promise<void>;
  listTools(): Promise<{
    tools: Array<{
      name: string;
      description?: string;
      inputSchema?: Record<string, unknown>;
    }>;
  }>;
  callTool(request: {
    name: string;
    arguments: Record<string, unknown>;
  }): Promise<{
    content?: Array<{ type: string; text?: string }>;
    structuredContent?: unknown;
    isError?: boolean;
  }>;
  close(): Promise<void>;
}

export interface McpClientDependencies {
  createClient(): McpClientLike;
  createTransport(url: URL): unknown;
}

export class McpTimeoutError extends Error {
  constructor(operation: string, timeoutMs: number) {
    super(`${operation} timed out after ${timeoutMs}ms`);
    this.name = "McpTimeoutError";
  }
}

export class McpAbortedError extends Error {
  constructor(operation: string) {
    super(`${operation} was aborted`);
    this.name = "McpAbortedError";
  }
}

const defaultDependencies: McpClientDependencies = {
  createClient: () =>
    new Client({ name: "tkb-pi-agent-adapter", version: "0.1.0" }) as McpClientLike,
  createTransport: (url) => new StreamableHTTPClientTransport(url),
};

async function withDeadline<T>(
  operation: string,
  timeoutMs: number,
  signal: AbortSignal | undefined,
  close: () => Promise<void>,
  task: () => Promise<T>,
): Promise<T> {
  if (signal?.aborted) throw new McpAbortedError(operation);

  let timeout: ReturnType<typeof setTimeout> | undefined;
  let abortHandler: (() => void) | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => {
      void close();
      reject(new McpTimeoutError(operation, timeoutMs));
    }, timeoutMs);
    if (signal) {
      abortHandler = () => {
        void close();
        reject(new McpAbortedError(operation));
      };
      signal.addEventListener("abort", abortHandler, { once: true });
    }
  });

  try {
    return await Promise.race([task(), deadline]);
  } finally {
    if (timeout) clearTimeout(timeout);
    if (signal && abortHandler) signal.removeEventListener("abort", abortHandler);
  }
}

function resultText(result: {
  content?: Array<{ type: string; text?: string }>;
  structuredContent?: unknown;
}): string {
  const text = (result.content ?? [])
    .filter((part) => part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
    .trim();
  if (text) return text;
  if (result.structuredContent !== undefined) {
    return JSON.stringify(result.structuredContent);
  }
  return "";
}

export class TkbMcpClient {
  constructor(
    private readonly config: TkbAdapterConfig = loadTkbAdapterConfig(),
    private readonly dependencies: McpClientDependencies = defaultDependencies,
  ) {}

  async listTools(signal?: AbortSignal): Promise<McpToolInfo[]> {
    return this.withConnectedClient(signal, this.config.defaultToolTimeoutMs, async (client) => {
      const result = await client.listTools();
      return result.tools.map((tool) => ({
        name: tool.name,
        description: tool.description ?? "",
        inputSchema: tool.inputSchema ?? {},
      }));
    });
  }

  async callTool(
    toolName: string,
    args: Record<string, unknown>,
    options: { signal?: AbortSignal; timeoutMs?: number } = {},
  ): Promise<McpCallResult> {
    return this.withConnectedClient(
      options.signal,
      options.timeoutMs ?? this.config.defaultToolTimeoutMs,
      async (client) => {
        const result = await client.callTool({ name: toolName, arguments: args });
        return { text: resultText(result), isError: result.isError === true };
      },
    );
  }

  private async withConnectedClient<T>(
    signal: AbortSignal | undefined,
    operationTimeoutMs: number,
    operation: (client: McpClientLike) => Promise<T>,
  ): Promise<T> {
    const client = this.dependencies.createClient();
    let closed = false;
    const close = async () => {
      if (closed) return;
      closed = true;
      await client.close().catch(() => undefined);
    };

    try {
      await withDeadline(
        "TKB MCP connect",
        this.config.connectTimeoutMs,
        signal,
        close,
        () => client.connect(this.dependencies.createTransport(new URL(this.config.mcpUrl))),
      );
      return await withDeadline(
        "TKB MCP operation",
        operationTimeoutMs,
        signal,
        close,
        () => operation(client),
      );
    } finally {
      await close();
    }
  }
}
