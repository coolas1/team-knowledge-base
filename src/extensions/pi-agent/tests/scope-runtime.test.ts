import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { loadPiAgentConfig, loadTkbAdapterConfig } from "../src/config.js";
import { ENGINE_MCP_CONTRACT, CONVERSATION_MEMORY_MCP_CONTRACT } from "../src/contract.js";
import { TkbMcpClient } from "../src/mcp-client.js";
import { PiAgentRuntime } from "../src/runtime.js";
import { RunnerClient } from "../src/runner-client.js";
import { ScopedRuntimeRegistry, scopeNamespace } from "../src/scope-runtime.js";
import { createPiAgentHttpServer } from "../src/server.js";
import { completedTurnDelivery, TranscriptStore } from "../src/transcript.js";

afterEach(() => vi.restoreAllMocks());
const digest = (token: string) => createHash("sha256").update(token).digest("hex");

it("normalizes binding defaults and does not use the credential as storage identity", () => {
  expect(scopeNamespace({})).toBe("default-team");
  expect(scopeNamespace({ bank_id: "default-team", write_tags: [] })).toBe("default-team");
  expect(scopeNamespace({ bank_id: "A", subject_id: "u" }))
    .toBe(scopeNamespace({ subject_id: "u", bank_id: "A", policy_version: 1 }));
  expect(scopeNamespace({ bank_id: "A", subject_id: "u" }))
    .not.toBe(scopeNamespace({ bank_id: "A", subject_id: "v" }));
});

it("isolates real SDK histories over HTTP, restarts and post-deletion forgetting", async () => {
  vi.spyOn(TkbMcpClient.prototype, "listTools").mockResolvedValue(
    Object.entries({ ...ENGINE_MCP_CONTRACT, ...CONVERSATION_MEMORY_MCP_CONTRACT })
      .map(([name, contract]) => ({ name, description: name, inputSchema: {
        type: "object", properties: Object.fromEntries(contract.required.map((key) => [key, { type: "string" }])),
        required: contract.required,
      } })),
  );
  vi.spyOn(RunnerClient.prototype, "health").mockResolvedValue({ available: false, capabilities: [], runtime: "test" });
  const forget = vi.spyOn(TkbMcpClient.prototype, "forgetConversationMemory").mockImplementation(async (id) => ({
    session_id: id, cancelled_jobs: 0, deleted_documents: 1,
  }));
  const recall = vi.spyOn(TkbMcpClient.prototype, "recallConversationMemory").mockImplementation(async function (this: TkbMcpClient) {
    return { memories: [{ memory_id: "m", text: `PRIVATE_SCOPE_${(this as any).config.scopeToken}_DATA`,
      memory_type: "world", document_id: "d", session_id: "s", turn_id: "t", score: 1, metadata: {} }], trace: {} };
  });
  const enqueue = vi.spyOn(TkbMcpClient.prototype, "enqueueConversationTurn").mockResolvedValue({ document_id: "d", status: "pending" });
  const prompts: string[] = [];
  const provider = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    prompts.push(Buffer.concat(chunks).toString());
    response.writeHead(200, { "content-type": "text/event-stream" });
    const event = (delta: unknown, finish_reason: string | null) => `data: ${JSON.stringify({
      id: "test", object: "chat.completion.chunk", created: 1, model: "test",
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`;
    response.end(event({ role: "assistant", content: "answer" }, null) + event({}, "stop") + "data: [DONE]\n\n");
  });
  await new Promise<void>((resolve) => provider.listen(0, "127.0.0.1", resolve));
  const modelUrl = `http://127.0.0.1:${(provider.address() as { port: number }).port}/v1`;
  const root = await mkdtemp(path.join(tmpdir(), "tkb-scoped-history-"));
  const config = loadPiAgentConfig({ PI_AGENT_CWD: root, PI_AGENT_DATA_DIR: root,
    PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_API_KEY: "test",
    PI_AGENT_TOOL_AUTHORING_ENABLED: "false", PI_AGENT_BASE_URL: modelUrl, PI_AGENT_REASONING: "false" });
  const adapter = loadTkbAdapterConfig({ TKB_CONVERSATION_MEMORY_ENABLED: "true" });
  const shared = new PiAgentRuntime(config, adapter);
  const bindings = JSON.stringify({ [digest("a")]: { bank_id: "A", subject_id: "u" },
    [digest("b")]: { bank_id: "A", subject_id: "v" },
    [digest("rotated")]: { subject_id: "u", bank_id: "A" } });
  let scopes = new ScopedRuntimeRegistry(shared, config, adapter, bindings);
  const deliveryScopes = new ScopedRuntimeRegistry(shared, config,
    { ...adapter, conversationMemoryReliableDelivery: true }, bindings);
  await expect(deliveryScopes.initializeDeliveryScopes("[]")).rejects.toThrow("startup credential");
  await expect(deliveryScopes.initializeDeliveryScopes("RAW_PRIVATE_TOKEN")).rejects.toThrow("must be valid JSON");
  await deliveryScopes.initializeDeliveryScopes('["a", "b"]');
  expect(await deliveryScopes.resolve("a")).not.toBe(shared);
  await deliveryScopes.close();
  await shared.initialize();
  const legacy = await shared.createSession();
  const server = createPiAgentHttpServer(shared, { scopes: { resolve: (token) => scopes.resolve(token) } });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/v1/sessions`;
  const request = (suffix = "", token?: string, method = "GET", body?: unknown) => fetch(base + suffix, {
    method, headers: token === undefined ? {} : { "x-tkb-scope-token": token },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  try {
    expect((await request("", "forged", "POST")).status).toBe(403);
    expect((await request("", "", "POST")).status).toBe(403);
    const [a, b] = await Promise.all([request("", "a", "POST"), request("", "b", "POST")]);
    expect([a.status, b.status]).toEqual([201, 201]);
    const aid = (await a.json()).id;
    const bid = (await b.json()).id;
    const owned = await scopes.resolve("a") as PiAgentRuntime;
    const second = await owned.createSession();
    // Inspect actual SDK sessions: clients and extension loaders are not shared.
    const managed = (owned as any).sessions;
    expect(managed.get(aid).mcpClient).not.toBe(managed.get(second.id).mcpClient);
    expect(managed.get(aid).mcpClient.config.scopeToken).toBe("a");
    const answers = await Promise.all([[aid, "a"], [bid, "b"]].map(async ([id, token]) => {
      const response = await request(`/${id}/messages`, token, "POST", { message: `question-${token}` });
      expect(response.status).toBe(200);
      return response.text();
    }));
    expect(answers.every((answer) => answer.includes("message.completed"))).toBe(true);
    for (const token of ["a", "b"]) {
      const prompt = prompts.find((prompt) => prompt.includes(`question-${token}`))!;
      expect(prompt).toContain(`PRIVATE_SCOPE_${token}_DATA`);
      expect(prompt).not.toContain(`PRIVATE_SCOPE_${token === "a" ? "b" : "a"}_DATA`);
      const index = enqueue.mock.calls.findIndex(([turn]) => turn.userText === `question-${token}`);
      const client = enqueue.mock.instances[index] as any;
      expect(client.config.scopeToken).toBe(token);
      expect(recall.mock.instances).toContain(client);
      expect(JSON.stringify(enqueue.mock.calls[index])).not.toContain("PRIVATE_SCOPE_");
    }
    for (const token of ["b", undefined]) {
      for (const [suffix, method] of [["", "GET"], ["", "DELETE"], ["/cancel", "POST"],
        ["/messages", "POST"], ["/memory", "DELETE"]]) {
        const response = await request(`/${aid}${suffix}`, token, method, method == "POST" ? { message: "do not save" } : undefined);
        expect(response.status).toBe(404);
        expect(response.headers.get("content-type")).not.toContain("event-stream");
      }
    }
    expect(forget).not.toHaveBeenCalled();
    const defaultList = await (await request()).json();
    expect(defaultList.items.map((item: { id: string }) => item.id)).toEqual([legacy.id]);
    const bList = await (await request("", "b")).json();
    expect(bList.items.map((item: { id: string }) => item.id)).toEqual([bid]);
    expect((await request(`/${legacy.id}`, "a")).status).toBe(404);
    expect(await scopes.resolve("rotated")).toBe(owned);
    await scopes.close();
    // Same persistent directories; new credential, new runtime instances.
    scopes = new ScopedRuntimeRegistry(shared, config, adapter,
      JSON.stringify({ [digest("rotated")]: { bank_id: "A", subject_id: "u" } }));
    expect((await request(`/${aid}`, "a")).status).toBe(403);
    expect((await request(`/${aid}`, "rotated")).status).toBe(200);
    expect((await request(`/${aid}`, "rotated", "DELETE")).status).toBe(200);
    expect((await request(`/${aid}`, "rotated")).status).toBe(404);
    await scopes.close();
    scopes = new ScopedRuntimeRegistry(shared, config, adapter, bindings);
    expect((await request(`/${aid}/memory`, "rotated", "DELETE")).status).toBe(200);
    expect((forget.mock.instances[0] as any).config.scopeToken).toBe("rotated");
    const marker = path.join(root, "scopes", scopeNamespace({ bank_id: "A", subject_id: "u" }),
      "transcripts", ".owners", aid);
    expect(await readFile(marker, "utf8")).not.toContain("rotated");
    await scopes.close();
    enqueue.mockRejectedValueOnce(new Error("MCP unavailable"));
    scopes = new ScopedRuntimeRegistry(shared, config, { ...adapter, conversationMemoryReliableDelivery: true }, bindings);
    await scopes.initializeDeliveryScopes('["a", "b"]');
    const reliable = await scopes.resolve("a") as PiAgentRuntime;
    const newSession = await reliable.createSession();
    const response = await request(`/${newSession.id}/messages`, "a", "POST", { message: "persist before delivery" });
    expect(await response.text()).toContain("message.completed");
    const journal = new TranscriptStore(reliable.config.transcriptDir);
    const saved = (await journal.snapshot(newSession.id))!.turns[0];
    expect(saved.deliveryResult?.status).toBe("pending");
    expect(saved.delivery).toEqual(completedTurnDelivery(
      scopeNamespace({ bank_id: "A", subject_id: "u" }), newSession.id, saved.id, "persist before delivery", "answer"));
  } finally {
    await scopes.close();
    await shared.close();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await new Promise<void>((resolve) => provider.close(() => resolve()));
  }
}, 30000);
