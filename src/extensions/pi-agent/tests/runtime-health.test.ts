import { afterEach, describe, expect, it, vi } from "vitest";
import { loadPiAgentConfig, loadTkbAdapterConfig } from "../src/config.js";
import { ENGINE_MCP_CONTRACT } from "../src/contract.js";
import { TkbMcpClient } from "../src/mcp-client.js";
import { PiAgentRuntime } from "../src/runtime.js";
import { RunnerClient } from "../src/runner-client.js";

afterEach(() => {
  vi.restoreAllMocks();
});

function memoryEnabledRuntime(): PiAgentRuntime {
  vi.spyOn(TkbMcpClient.prototype, "listTools").mockResolvedValue(
    Object.entries(ENGINE_MCP_CONTRACT).map(([name, contract]) => ({
      name,
      description: name,
      inputSchema: {
        type: "object",
        properties: Object.fromEntries(
          contract.required.map((key) => [key, { type: "string" }]),
        ),
        required: contract.required,
      },
    })),
  );
  vi.spyOn(RunnerClient.prototype, "health").mockResolvedValue({
    available: false,
    capabilities: [],
    runtime: "test",
  });

  const runtime = new PiAgentRuntime(
    loadPiAgentConfig(),
    loadTkbAdapterConfig({ TKB_CONVERSATION_MEMORY_ENABLED: "true" }),
  );
  // 跳过 initialize() 的重活（模型服务/resource loader），只测 health 分支。
  const privateRuntime = runtime as unknown as {
    modelServices: unknown;
    resourceLoader: unknown;
  };
  privateRuntime.modelServices = {};
  privateRuntime.resourceLoader = {};
  return runtime;
}

describe("conversation memory health reporting", () => {
  it("reports an unreachable status as unavailable, not as a failed job", async () => {
    const runtime = memoryEnabledRuntime();
    vi.spyOn(
      TkbMcpClient.prototype,
      "getConversationMemoryStatus",
    ).mockRejectedValue(new Error("status endpoint down"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    const health = await runtime.health();

    expect(health.conversationMemory).toMatchObject({
      enabled: true,
      failed: 0, // 不伪造 failed: 1
      unavailable: true,
    });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("conversation_memory_status_unavailable"),
    );
  });

  it("logs a swallowed retention failure", async () => {
    const runtime = memoryEnabledRuntime();
    vi.spyOn(
      TkbMcpClient.prototype,
      "enqueueConversationTurn",
    ).mockRejectedValue(new Error("retention endpoint down"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

    vi.spyOn(runtime as any, "loadSession").mockResolvedValue({
      mcpClient: new TkbMcpClient(loadTkbAdapterConfig()),
    });
    vi.spyOn(runtime as any, "findSubmissionByTurn").mockResolvedValue(undefined);

    await (
      runtime as unknown as {
        enqueueCompletedTurn: (
          sessionId: string,
          turnId: string,
          userText: string,
          assistantText: string,
        ) => Promise<void>;
      }
    ).enqueueCompletedTurn("s1", "t1", "question", "answer");

    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("conversation_memory_retention_failed"),
    );
    expect(warn.mock.calls[0][0]).toContain("retention endpoint down");
  });
});
