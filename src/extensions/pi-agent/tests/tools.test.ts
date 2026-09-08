import { describe, expect, it, vi } from "vitest";
import { loadTkbAdapterConfig } from "../src/config.js";
import { SearchFallbackBudget, TurnDeadlineBudget } from "../src/budget.js";
import {
  McpAbortedError,
  McpTimeoutError,
  type TkbMcpClient,
} from "../src/mcp-client.js";
import { buildAllTkbTools, enabledTkbTools } from "../src/tools.js";

function client() {
  return {
    callTool: vi.fn(async () => ({ text: '{"chunks":[]}', isError: false })),
  } as unknown as TkbMcpClient;
}

describe("TKB Pi tools", () => {
  it("maps all eleven engine MCP tools", () => {
    const tools = buildAllTkbTools({ client: client() });
    expect(tools).toHaveLength(11);
    expect(tools.map((tool) => tool.name)).not.toEqual(
      expect.arrayContaining([
        "recall_conversation_memory",
        "enqueue_conversation_turn",
        "forget_conversation_memory",
      ]),
    );
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
      "tkb_generate_document",
    ]);
  });

  it("generates documents through the MCP tool", async () => {
    const fake = client();
    const tool = buildAllTkbTools({ client: fake }).find(
      (candidate) => candidate.name === "tkb_generate_document",
    )!;
    await tool.execute(
      "call-2",
      { format: "pptx", title: "Roadmap", content: "# Q1", file_name: "roadmap" },
      undefined,
      undefined,
      {} as never,
    );
    expect(fake.callTool).toHaveBeenCalledWith(
      "generate_document",
      { format: "pptx", title: "Roadmap", content: "# Q1", file_name: "roadmap" },
      expect.any(Object),
    );
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

  it("uses one fast fallback after a typed deep timeout", async () => {
    const fake = {
      callTool: vi
        .fn()
        .mockResolvedValueOnce({
          text: JSON.stringify({
            sources: [],
            trace: { degraded: true },
            error: { code: "deep_search_timeout" },
          }),
          isError: false,
        })
        .mockResolvedValueOnce({
          text: JSON.stringify({
            sources: [{ doc_id: "d1", title: "Status", chunk_text: "on track" }],
            trace: {},
          }),
          isError: false,
        }),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({
      client: fake,
      fallbackBudget: new SearchFallbackBudget(),
      turnDeadline: new TurnDeadlineBudget(180, 60),
    }).find((candidate) => candidate.name === "tkb_search_deep")!;

    const result = await tool.execute(
      "deep-1",
      { query: "compare" },
      undefined,
      undefined,
      {} as never,
    );

    expect(fake.callTool).toHaveBeenCalledTimes(2);
    expect(fake.callTool).toHaveBeenNthCalledWith(
      1,
      "search_knowledge_deep",
      expect.objectContaining({ query: "compare", correlation_id: expect.any(String) }),
      expect.objectContaining({ timeoutMs: 60_000 }),
    );
    expect(fake.callTool).toHaveBeenNthCalledWith(
      2,
      "search_knowledge_fast",
      { query: "compare", top_k: 10 },
      expect.objectContaining({ timeoutMs: 60_000 }),
    );
    const typed = result as { content: Array<{ text: string }>; details: Record<string, unknown> };
    expect(JSON.parse(typed.content[0]!.text)).toMatchObject({
      fallback_from: "deep",
      sources: [{ doc_id: "d1" }],
      trace: { degraded: true, fallback: { from: "deep" } },
    });
    expect(typed.details).toMatchObject({ activity: "fallback", fallback: true });
  });

  it("does not fall back after cancellation", async () => {
    const fake = {
      callTool: vi.fn(async () => {
        throw new McpAbortedError("deep search");
      }),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({
      client: fake,
      fallbackBudget: new SearchFallbackBudget(),
    }).find((candidate) => candidate.name === "tkb_search_deep")!;
    const controller = new AbortController();
    controller.abort();

    await expect(
      tool.execute(
        "deep-cancel",
        { query: "compare" },
        controller.signal,
        undefined,
        {} as never,
      ),
    ).rejects.toThrow(/aborted/);
    expect(fake.callTool).toHaveBeenCalledOnce();
  });

  it("falls back after the Pi MCP deadline fires", async () => {
    const fake = {
      callTool: vi
        .fn()
        .mockRejectedValueOnce(new McpTimeoutError("deep search", 60_000))
        .mockResolvedValueOnce({ text: '{"sources":[]}', isError: false }),
    } as unknown as TkbMcpClient;
    const tool = buildAllTkbTools({
      client: fake,
      fallbackBudget: new SearchFallbackBudget(),
    }).find((candidate) => candidate.name === "tkb_search_deep")!;

    const result = await tool.execute(
      "deep-timeout",
      { query: "compare" },
      undefined,
      undefined,
      {} as never,
    );

    expect(fake.callTool).toHaveBeenCalledTimes(2);
    expect((result as { details: Record<string, unknown> }).details.fallback).toBe(true);
  });

  it("does not repeat an automatic fallback in one turn", async () => {
    const unavailable = {
      text: JSON.stringify({
        sources: [],
        error: { code: "deep_search_unavailable" },
      }),
      isError: false,
    };
    const fake = {
      callTool: vi
        .fn()
        .mockResolvedValueOnce(unavailable)
        .mockResolvedValueOnce({ text: '{"sources":[]}', isError: false })
        .mockResolvedValueOnce(unavailable),
    } as unknown as TkbMcpClient;
    const fallbackBudget = new SearchFallbackBudget();
    const tool = buildAllTkbTools({ client: fake, fallbackBudget }).find(
      (candidate) => candidate.name === "tkb_search_deep",
    )!;

    await tool.execute("deep-1", { query: "first" }, undefined, undefined, {} as never);
    await expect(
      tool.execute("deep-2", { query: "second" }, undefined, undefined, {} as never),
    ).rejects.toThrow(/fallback already used/);
    expect(fake.callTool).toHaveBeenCalledTimes(3);
  });
});
