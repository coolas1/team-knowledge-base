import { createServer } from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { PiAgentRuntime, type PiRuntimeEvent } from "../src/runtime.js";
import { loadPiAgentConfig, loadTkbAdapterConfig } from "../src/config.js";
import { TkbMcpClient } from "../src/mcp-client.js";
import { ENGINE_MCP_CONTRACT } from "../src/contract.js";
import { RunnerClient } from "../src/runner-client.js";

afterEach(() => vi.restoreAllMocks());
it("uses the real SDK loop for error history, safe SSE, publication and immediate reuse", async () => {
  const logs: string[] = [];
  vi.spyOn(console, "info").mockImplementation((value) => logs.push(String(value)));
  vi.spyOn(TkbMcpClient.prototype, "listTools").mockResolvedValue(Object.entries(ENGINE_MCP_CONTRACT).map(([name, c]) => ({ name, description: name, inputSchema: { type: "object", properties: Object.fromEntries(c.required.map(k => [k, { type: "string" }])), required: c.required } })));
  vi.spyOn(TkbMcpClient.prototype, "callTool").mockResolvedValue({ text: "provider failed", isError: true, content: [] } as never);
  vi.spyOn(TkbMcpClient.prototype, "recallConversationMemory").mockResolvedValue({ memories: [], trace: {} });
  const enqueueTurn = vi.spyOn(TkbMcpClient.prototype, "enqueueConversationTurn")
    .mockResolvedValue({ document_id: "conversation-turn", status: "pending" });
  vi.spyOn(RunnerClient.prototype, "health").mockResolvedValue({ available: true, capabilities: [], runtime: "test" });
  vi.spyOn(RunnerClient.prototype, "run").mockImplementation(async job => ({ jobId: "12345678-1234-1234-1234-123456789abc", status: "succeeded", result: (job.input as { n: number }).n * 2, runtime: "test", logs: "" }));
  const params = { action: "publish", name: "gen_sdk_double", description: "Double a number", expectedVersion: 0, code: "export default input=>input.n*2 // PRIVATE_SOURCE", inputSchema: { type: "object", properties: { n: { type: "number" } }, required: ["n"] }, outputSchema: { type: "number" }, capabilities: [], tests: [{ input: { n: 2 }, expected: 4 }, { input: { n: 0 }, expected: 0 }] };
  const calls = [
    { name: "tkb_search_fast", arguments: { query: "failure" } },
    { name: "read", arguments: { path: "../../private.md" } },
    { name: "call_tool", arguments: { name: "gen_missing", version: 1, input: {} } },
    { name: "publish_tool", arguments: params },
    { name: "call_tool", arguments: { name: "gen_sdk_double", version: 1, input: { n: 9 } } },
  ];
  const requests: any[] = []; let step = 0;
  const server = createServer(async (req, res) => {
    const chunks: Buffer[] = []; for await (const chunk of req) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()));
    res.writeHead(200, { "content-type": "text/event-stream" });
    const call = calls[step++];
    const delta = call ? { role: "assistant", tool_calls: [{ index: 0, id: `call_${step}`, type: "function", function: { name: call.name, arguments: JSON.stringify(call.arguments) } }] } : { role: "assistant", content: "18" };
    res.write(`data: ${JSON.stringify({ id: "test", object: "chat.completion.chunk", created: 1, model: "test", choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`);
    res.end(`data: ${JSON.stringify({ id: "test", object: "chat.completion.chunk", created: 1, model: "test", choices: [{ index: 0, delta: {}, finish_reason: call ? "tool_calls" : "stop" }] })}\n\ndata: [DONE]\n\n`);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/v1`;
  const data = mkdtempSync(path.join(tmpdir(), "tkb-sdk-"));
  const config = loadPiAgentConfig({ PI_AGENT_DATA_DIR: data, PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_BASE_URL: base, PI_AGENT_API_KEY: "test", PI_AGENT_REASONING: "false", PI_AGENT_EXPOSE_TOOL_RESULTS: "true" });
  const adapter = loadTkbAdapterConfig({
    TKB_CONVERSATION_MEMORY_ENABLED: "true",
    TKB_CONTRACT_STRICT: "false",
  });
  const runtime = new PiAgentRuntime(config, adapter);
  let resumed: PiAgentRuntime | undefined;
  try {
    await runtime.initialize(); const session = await runtime.createSession(); const events: PiRuntimeEvent[] = [];
    await runtime.streamMessage(session.id, "Create and reuse a tool", e => { events.push(e); }, "client-create");
    expect(events[0]).toMatchObject({ type: "message.accepted", clientMessageId: "client-create" });
    const results = events.filter(e => e.type === "tool.result");
    expect(results.map(e => e.isError)).toEqual([true, true, true, false, false]);
    expect(results.at(-1)).toMatchObject({ activity: "reuse", artifactId: "gen_sdk_double", version: 1 });
    expect(JSON.stringify(events)).not.toContain("PRIVATE_SOURCE");
    expect(JSON.stringify(events)).not.toContain('"n":9');
    const managed = (runtime as any).sessions.get(session.id);
    expect(managed.session.messages.filter((m: any) => m.role === "toolResult").map((m: any) => m.isError)).toEqual([true, true, true, false, false]);
    expect(JSON.stringify(requests[0].messages)).toContain("自主编写");
    expect(JSON.stringify(requests[0].messages)).toContain("agent-tool-authoring");
    expect(events.at(-1)).toMatchObject({ type: "message.completed", answer: "18" });
    expect(enqueueTurn).toHaveBeenCalledOnce();
    expect(enqueueTurn).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: session.id,
      turnId: expect.any(String),
      userText: "Create and reuse a tool",
      assistantText: "18",
    }), expect.any(Object));
    const completedDetail = await runtime.getSession(session.id);
    expect(completedDetail.messageCount).toBe(completedDetail.messages.length);
    expect(completedDetail.messages.map(message => [message.role, message.status])).toEqual([
      ["user", "completed"], ["assistant", "completed"],
    ]);
    const requestCount = requests.length;
    const replayEvents: PiRuntimeEvent[] = [];
    await runtime.streamMessage(session.id, "Create and reuse a tool", event => { replayEvents.push(event); }, "client-create");
    expect(requests).toHaveLength(requestCount);
    expect(replayEvents[0]).toMatchObject({ type: "message.accepted", replayed: true, status: "completed" });
    expect(replayEvents.at(-1)).toMatchObject({ type: "message.completed", answer: "18" });
    // Repeated calls above the limit terminate the SDK loop rather than allowing another model retry.
    calls.splice(0, calls.length, ...Array.from({ length: 4 }, () => ({ name: "tkb_search_fast", arguments: { query: "failure" } }) as typeof calls[number]));
    step = 0; (managed.budget as any).maxToolCalls = 1;
    await expect(runtime.streamMessage(session.id, "Try again", () => {}, "client-failed")).rejects.toThrow("tool call limit");
    expect(step).toBe(2);
    expect(managed.session.messages.at(-1).isError).toBe(true);
    const failedDetail = await runtime.getSession(session.id);
    expect(failedDetail.messages.at(-1)).toMatchObject({
      role: "user", text: "Try again", status: "failed", clientMessageId: "client-failed",
    });
    expect(enqueueTurn).toHaveBeenCalledOnce();
    expect(JSON.stringify(logs)).not.toContain("Create and reuse a tool");
    expect(JSON.stringify(logs)).not.toContain("Try again");

    await runtime.close();
    resumed = new PiAgentRuntime(config, adapter);
    await resumed.initialize();
    const restored = await resumed.getSession(session.id);
    expect(restored.messages).toEqual(failedDetail.messages);
    expect((await resumed.listSessions()).find(item => item.id === session.id)?.messageCount)
      .toBe(restored.messages.length);
    for (const line of logs) {
      const record = JSON.parse(line) as Record<string, unknown>;
      expect(Object.keys(record).sort()).toEqual(
        expect.arrayContaining(["event", "session_id", "status"]),
      );
      expect(Object.keys(record).every((key) =>
        ["event", "session_id", "turn_id", "status", "code"].includes(key),
      )).toBe(true);
    }
  } finally { await runtime.close(); await resumed?.close(); await new Promise<void>(resolve => server.close(() => resolve())); }
}, 30000);

it("streams deep-search fallback state through completion", async () => {
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
  vi.spyOn(TkbMcpClient.prototype, "callTool").mockImplementation(
    async (name) => {
      if (name === "search_knowledge_deep") {
        return {
          text: JSON.stringify({
            sources: [],
            trace: { degraded: true },
            error: { code: "deep_search_timeout" },
          }),
          isError: false,
        };
      }
      if (name === "search_knowledge_fast") {
        return {
          text: JSON.stringify({
            sources: [{ doc_id: "doc-1", title: "Status", chunk_text: "on track" }],
            trace: {},
          }),
          isError: false,
        };
      }
      throw new Error(`unexpected tool: ${name}`);
    },
  );
  vi.spyOn(RunnerClient.prototype, "health").mockResolvedValue({
    available: false,
    capabilities: [],
    runtime: "test",
  });

  let step = 0;
  const server = createServer(async (_req, res) => {
    res.writeHead(200, { "content-type": "text/event-stream" });
    const delta =
      step++ === 0
        ? {
            role: "assistant",
            tool_calls: [
              {
                index: 0,
                id: "call_deep",
                type: "function",
                function: {
                  name: "tkb_search_deep",
                  arguments: JSON.stringify({ query: "compare" }),
                },
              },
            ],
          }
        : { role: "assistant", content: "Used degraded fallback evidence." };
    res.write(
      `data: ${JSON.stringify({ id: "test", object: "chat.completion.chunk", created: 1, model: "test", choices: [{ index: 0, delta, finish_reason: null }] })}\n\n`,
    );
    res.end(
      `data: ${JSON.stringify({ id: "test", object: "chat.completion.chunk", created: 1, model: "test", choices: [{ index: 0, delta: {}, finish_reason: step === 1 ? "tool_calls" : "stop" }] })}\n\ndata: [DONE]\n\n`,
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/v1`;
  const data = mkdtempSync(path.join(tmpdir(), "tkb-deep-fallback-"));
  const runtime = new PiAgentRuntime(
    loadPiAgentConfig({
      PI_AGENT_DATA_DIR: data,
      PI_AGENT_PROVIDER: "test",
      PI_AGENT_MODEL: "test",
      PI_AGENT_BASE_URL: base,
      PI_AGENT_API_KEY: "test",
      PI_AGENT_REASONING: "false",
      PI_AGENT_TOOL_AUTHORING_ENABLED: "false",
    }),
    loadTkbAdapterConfig({}),
  );
  try {
    await runtime.initialize();
    const session = await runtime.createSession();
    const events: PiRuntimeEvent[] = [];
    await runtime.streamMessage(session.id, "Compare project status", (event) => {
      events.push(event);
    });

    const lifecycle = events.filter((event) =>
      ["tool.start", "tool.result", "message.completed"].includes(event.type),
    );
    expect(lifecycle.map((event) => event.type)).toEqual([
      "tool.start",
      "tool.result",
      "message.completed",
    ]);
    expect(lifecycle[1]).toMatchObject({
      type: "tool.result",
      toolName: "tkb_search_deep",
      activity: "fallback",
      isError: false,
    });
    expect(lifecycle[2]).toMatchObject({
      type: "message.completed",
      searchDegraded: true,
      searchFallback: true,
    });
  } finally {
    await runtime.close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
}, 30000);
