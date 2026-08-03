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
});
