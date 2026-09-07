import { describe, expect, it, vi } from "vitest";
import { loadTkbAdapterConfig } from "../src/config.js";
import {
  McpAbortedError,
  McpTimeoutError,
  TkbMcpClient,
  type McpClientLike,
} from "../src/mcp-client.js";

function dependencies(client: McpClientLike) {
  return { createClient: () => client, createTransport: () => ({}) };
}

function fakeClient(overrides: Partial<McpClientLike> = {}): McpClientLike {
  return {
    connect: vi.fn(async () => undefined),
    listTools: vi.fn(async () => ({ tools: [] })),
    callTool: vi.fn(async () => ({ content: [{ type: "text", text: "ok" }] })),
    close: vi.fn(async () => undefined),
    ...overrides,
  };
}

describe("TkbMcpClient", () => {
  it("returns MCP text and closes the connection", async () => {
    const raw = fakeClient();
    const client = new TkbMcpClient(loadTkbAdapterConfig({}), dependencies(raw));
    await expect(client.callTool("search", { query: "x" })).resolves.toEqual({
      text: "ok",
      isError: false,
    });
    expect(raw.close).toHaveBeenCalledOnce();
  });

  it("times out and closes a stalled operation", async () => {
    const raw = fakeClient({
      callTool: vi.fn(
        () =>
          new Promise<Awaited<ReturnType<McpClientLike["callTool"]>>>(
            () => undefined,
          ),
      ),
    });
    const client = new TkbMcpClient(
      loadTkbAdapterConfig({ TKB_TOOL_TIMEOUT_MS: "10" }),
      dependencies(raw),
    );
    await expect(client.callTool("search", {})).rejects.toBeInstanceOf(McpTimeoutError);
    expect(raw.close).toHaveBeenCalledOnce();
  });

  it("propagates cancellation and closes the connection", async () => {
    const raw = fakeClient({
      callTool: vi.fn(
        () =>
          new Promise<Awaited<ReturnType<McpClientLike["callTool"]>>>(
            () => undefined,
          ),
      ),
    });
    const client = new TkbMcpClient(loadTkbAdapterConfig({}), dependencies(raw));
    const controller = new AbortController();
    const pending = client.callTool("search", {}, { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toBeInstanceOf(McpAbortedError);
    expect(raw.close).toHaveBeenCalledOnce();
  });

  it("maps internal conversation-memory calls and parses JSON responses", async () => {
    const raw = fakeClient({
      callTool: vi
        .fn()
        .mockResolvedValueOnce({
          content: [{ type: "text", text: JSON.stringify({ memories: [], trace: {} }) }],
        })
        .mockResolvedValueOnce({
          content: [{ type: "text", text: JSON.stringify({ document_id: "d1", status: "pending" }) }],
        })
        .mockResolvedValueOnce({
          content: [{ type: "text", text: JSON.stringify({ session_id: "s1", cancelled_jobs: 1, deleted_documents: 2 }) }],
        })
        .mockResolvedValueOnce({
          content: [{ type: "text", text: JSON.stringify({ enabled: true, pending: 1, processing: 0, completed: 2, failed: 0, cancelled: 0 }) }],
        }),
    });
    const client = new TkbMcpClient(loadTkbAdapterConfig({}), dependencies(raw));

    await expect(client.recallConversationMemory("remember", { topK: 3 })).resolves.toEqual({
      memories: [],
      trace: {},
    });
    await expect(
      client.enqueueConversationTurn({
        sessionId: "s1",
        turnId: "t1",
        userText: "question",
        assistantText: "answer",
      }),
    ).resolves.toEqual({ document_id: "d1", status: "pending" });
    await expect(client.forgetConversationMemory("s1")).resolves.toEqual({
      session_id: "s1",
      cancelled_jobs: 1,
      deleted_documents: 2,
    });
    await expect(client.getConversationMemoryStatus()).resolves.toMatchObject({
      enabled: true,
      pending: 1,
    });
    expect(raw.callTool).toHaveBeenNthCalledWith(
      1,
      { name: "recall_conversation_memory", arguments: { query: "remember", top_k: 3, mode: "fast" } },
    );
    expect(raw.callTool).toHaveBeenNthCalledWith(
      2,
      {
        name: "enqueue_conversation_turn",
        arguments: { session_id: "s1", turn_id: "t1", user_text: "question", assistant_text: "answer" },
      },
    );
  });
});
