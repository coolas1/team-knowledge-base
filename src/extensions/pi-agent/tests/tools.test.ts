import { describe, expect, it, vi } from "vitest";
import { loadTkbAdapterConfig } from "../src/config.js";
import type { TkbMcpClient } from "../src/mcp-client.js";
import { buildAllTkbTools, enabledTkbTools } from "../src/tools.js";

function client() {
  return {
    callTool: vi.fn(async () => ({ text: '{"chunks":[]}', isError: false })),
  } as unknown as TkbMcpClient;
}

describe("TKB Pi tools", () => {
  it("maps all ten engine MCP tools", () => {
    expect(buildAllTkbTools({ client: client() })).toHaveLength(10);
  });

  it("enables only the safe curated tools by default", () => {
    const tools = enabledTkbTools({
      client: client(),
      config: loadTkbAdapterConfig({}),
    });
    expect(tools.map((tool) => tool.name)).toEqual([
      "tkb_query_knowledge",
      "tkb_search_fast",
      "tkb_search_deep",
      "tkb_get_document",
      "tkb_query_graph",
      "tkb_list_documents",
    ]);
  });

  it("maps fast search parameters and forwards cancellation", async () => {
    const fake = client();
    const tool = buildAllTkbTools({ client: fake }).find(
      (candidate) => candidate.name === "tkb_search_fast",
    )!;
    const controller = new AbortController();
    const result = await tool.execute(
      "call-1",
      { query: "weekly report", top_k: 7 },
      controller.signal,
      undefined,
      {} as never,
    );
    expect(fake.callTool).toHaveBeenCalledWith(
      "search_knowledge_fast",
      { query: "weekly report", top_k: 7 },
      expect.objectContaining({ signal: controller.signal }),
    );
    expect((result as { isError?: boolean }).isError).toBe(false);
  });
});
