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
  vi.spyOn(TkbMcpClient.prototype, "listTools").mockResolvedValue(Object.entries(ENGINE_MCP_CONTRACT).map(([name, c]) => ({ name, description: name, inputSchema: { type: "object", properties: Object.fromEntries(c.required.map(k => [k, { type: "string" }])), required: c.required } })));
  vi.spyOn(TkbMcpClient.prototype, "callTool").mockResolvedValue({ text: "provider failed", isError: true, content: [] } as never);
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
  const runtime = new PiAgentRuntime(loadPiAgentConfig({ PI_AGENT_DATA_DIR: data, PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_BASE_URL: base, PI_AGENT_API_KEY: "test", PI_AGENT_REASONING: "false", PI_AGENT_EXPOSE_TOOL_RESULTS: "true" }), loadTkbAdapterConfig({}));
  try {
    await runtime.initialize(); const session = await runtime.createSession(); const events: PiRuntimeEvent[] = [];
    await runtime.streamMessage(session.id, "Create and reuse a tool", e => { events.push(e); });
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
    // Repeated calls above the limit terminate the SDK loop rather than allowing another model retry.
    calls.splice(0, calls.length, ...Array.from({ length: 4 }, () => ({ name: "tkb_search_fast", arguments: { query: "failure" } }) as typeof calls[number]));
    step = 0; (managed.budget as any).maxToolCalls = 1;
    await expect(runtime.streamMessage(session.id, "Try again", () => {})).rejects.toThrow("tool call limit");
    expect(step).toBe(2);
    expect(managed.session.messages.at(-1).isError).toBe(true);
  } finally { await runtime.close(); await new Promise<void>(resolve => server.close(() => resolve())); }
}, 30000);
