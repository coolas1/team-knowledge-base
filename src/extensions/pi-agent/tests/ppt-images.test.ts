import { describe, expect, it, vi } from "vitest";
import { loadTkbAdapterConfig, loadPiAgentConfig } from "../src/config.js";
import { TkbMcpClient } from "../src/mcp-client.js";
import { buildAllTkbTools } from "../src/tools.js";

describe("PPT image context", () => {
  it("preserves real MCP image blocks through the Pi tool result", async () => {
    const block = { type: "image" as const, data: "aW1hZ2U=", mimeType: "image/png" };
    const raw = { connect: vi.fn(async () => {}), close: vi.fn(async () => {}), listTools: vi.fn(),
      callTool: vi.fn(async () => ({ content: [block] })),
    };
    const client = new TkbMcpClient(loadTkbAdapterConfig({}), { createClient: () => raw, createTransport: () => ({}) });
    const tool = buildAllTkbTools({ client }).find(x => x.name === "tkb_preview_ppt")!;
    const result = await tool.execute("call", { identifier: "job", page: 1 }, undefined, undefined, {} as never);
    expect(result.content).toContainEqual(block);
    expect(raw.callTool).toHaveBeenCalledWith({ name: "preview_ppt", arguments: { identifier: "job", page: 1 } });
  });
  it("enables vision only through explicit capability configuration", () => {
    expect(loadPiAgentConfig({ PI_AGENT_IMAGE_INPUT: "true" }).modelImageInput).toBe(true);
    expect(loadPiAgentConfig({}).modelImageInput).toBe(false);
  });
});
