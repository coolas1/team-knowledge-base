import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadPiAgentConfig, loadTkbAdapterConfig } from "../src/config.js";
import { ENGINE_MCP_CONTRACT } from "../src/contract.js";
import { TkbMcpClient } from "../src/mcp-client.js";
import { PiAgentRuntime, type PiRuntimeEvent } from "../src/runtime.js";
import { RunnerClient } from "../src/runner-client.js";

const servers: Server[] = [];

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((resolve) => server.close(() => resolve()))));
});

function mockDependencies(): void {
  vi.spyOn(TkbMcpClient.prototype, "listTools").mockResolvedValue(
    Object.entries(ENGINE_MCP_CONTRACT).map(([name, contract]) => ({
      name,
      description: name,
      inputSchema: {
        type: "object",
        properties: Object.fromEntries(contract.required.map((key) => [key, { type: "string" }])),
        required: contract.required,
      },
    })),
  );
  vi.spyOn(RunnerClient.prototype, "health").mockResolvedValue({ available: false, capabilities: [], runtime: "test" });
}

function assistant(text: string) {
  return {
    role: "assistant" as const,
    content: [{ type: "text" as const, text }],
    api: "openai-completions" as const,
    provider: "test",
    model: "test",
    usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop" as const,
    timestamp: Date.now(),
  };
}

async function listen(handler: (request: IncomingMessage, response: ServerResponse) => void): Promise<string> {
  const server = createServer(handler);
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${(server.address() as { port: number }).port}/v1`;
}

describe("runtime transcript integrity", () => {
  it("recovers a compacted active branch without changing the SDK JSONL", async () => {
    mockDependencies();
    const root = await mkdtemp(path.join(tmpdir(), "tkb-runtime-history-"));
    const sessionDir = path.join(root, "sessions");
    const manager = SessionManager.create(root, sessionDir);
    const first = manager.appendMessage({ role: "user", content: "first question", timestamp: Date.now() });
    manager.appendMessage(assistant("first answer"));
    manager.appendCompaction("summary", first, 100);
    manager.appendMessage({ role: "user", content: "second question", timestamp: Date.now() });
    manager.appendMessage(assistant("second answer"));
    const sourceBefore = await readFile(manager.getSessionFile()!, "utf8");
    const runtime = new PiAgentRuntime(loadPiAgentConfig({
      PI_AGENT_CWD: root,
      PI_AGENT_DATA_DIR: root,
      PI_AGENT_SESSION_DIR: sessionDir,
      PI_AGENT_TRANSCRIPT_DIR: path.join(root, "transcripts"),
      PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_API_KEY: "test",
      PI_AGENT_TOOL_AUTHORING_ENABLED: "false",
    }), loadTkbAdapterConfig({}));
    try {
      await runtime.initialize();
      const summaries = await runtime.listSessions();
      expect(summaries[0].messageCount).toBe(4);
      expect(await readFile(manager.getSessionFile()!, "utf8")).toBe(sourceBefore);
      const detail = await runtime.getSession(manager.getSessionId());
      expect(detail.messages.map((message) => message.text)).toEqual([
        "first question", "first answer", "second question", "second answer",
      ]);
      expect(detail.messageCount).toBe(detail.messages.length);
      expect((await runtime.listSessions())[0].messageCount).toBe(detail.messages.length);
    } finally {
      await runtime.close();
    }
  });

  it("retains an accepted user message when the provider fails", async () => {
    mockDependencies();
    const base = await listen((_request, response) => {
      response.writeHead(401, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: { message: "provider secret", type: "authentication_error" } }));
    });
    const root = await mkdtemp(path.join(tmpdir(), "tkb-runtime-failure-"));
    const runtime = new PiAgentRuntime(loadPiAgentConfig({
      PI_AGENT_CWD: root, PI_AGENT_DATA_DIR: root,
      PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_BASE_URL: base,
      PI_AGENT_API_KEY: "test", PI_AGENT_REASONING: "false",
      PI_AGENT_TOOL_AUTHORING_ENABLED: "false",
    }), loadTkbAdapterConfig({}));
    try {
      await runtime.initialize();
      const session = await runtime.createSession();
      const events: PiRuntimeEvent[] = [];
      await expect(runtime.streamMessage(session.id, "keep this", (event) => { events.push(event); }, "client-failure"))
        .rejects.toThrow();
      expect(events[0]).toMatchObject({ type: "message.accepted", clientMessageId: "client-failure" });
      expect(events.at(-1)).toMatchObject({
        type: "message.failed", status: "failed", code: "agent_failed",
        clientMessageId: "client-failure",
      });
      expect((await runtime.getSession(session.id)).messages).toEqual([
        expect.objectContaining({ role: "user", text: "keep this", status: "failed" }),
      ]);
      expect(JSON.stringify(events)).not.toContain("provider secret");
    } finally {
      await runtime.close();
    }
  });

  it("retains an accepted user message after cancellation", async () => {
    mockDependencies();
    let requestStarted!: () => void;
    const started = new Promise<void>((resolve) => { requestStarted = resolve; });
    let providerRequests = 0;
    const base = await listen((_request, response) => {
      providerRequests++;
      response.writeHead(200, { "content-type": "text/event-stream" });
      response.flushHeaders();
      requestStarted();
    });
    const root = await mkdtemp(path.join(tmpdir(), "tkb-runtime-cancel-"));
    const runtime = new PiAgentRuntime(loadPiAgentConfig({
      PI_AGENT_CWD: root, PI_AGENT_DATA_DIR: root,
      PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_BASE_URL: base,
      PI_AGENT_API_KEY: "test", PI_AGENT_REASONING: "false",
      PI_AGENT_TOOL_AUTHORING_ENABLED: "false",
    }), loadTkbAdapterConfig({}));
    try {
      await runtime.initialize();
      const session = await runtime.createSession();
      const running = runtime.streamMessage(session.id, "cancel but keep", () => {}, "client-cancel");
      await started;
      const replayEvents: PiRuntimeEvent[] = [];
      await runtime.streamMessage(session.id, "cancel but keep", (event) => {
        replayEvents.push(event);
      }, "client-cancel");
      expect(replayEvents).toEqual([
        expect.objectContaining({ type: "message.accepted", replayed: true, status: "running" }),
      ]);
      expect(providerRequests).toBe(1);
      expect(await runtime.cancel(session.id)).toBe(true);
      await expect(running).rejects.toThrow("cancelled");
      expect((await runtime.getSession(session.id)).messages).toEqual([
        expect.objectContaining({ role: "user", text: "cancel but keep", status: "cancelled" }),
      ]);
    } finally {
      await runtime.close();
    }
  });

  it("supports legacy IDs, rejects unsafe IDs, and persists a preflight failure", async () => {
    mockDependencies();
    const root = await mkdtemp(path.join(tmpdir(), "tkb-runtime-preflight-"));
    const runtime = new PiAgentRuntime(loadPiAgentConfig({
      PI_AGENT_CWD: root, PI_AGENT_DATA_DIR: root,
      PI_AGENT_PROVIDER: "test", PI_AGENT_MODEL: "test", PI_AGENT_API_KEY: "test",
      PI_AGENT_TOOL_AUTHORING_ENABLED: "false",
    }), loadTkbAdapterConfig({}));
    try {
      await runtime.initialize();
      const session = await runtime.createSession();
      const managed = (runtime as any).sessions.get(session.id);
      const prompt = vi.spyOn(managed.session, "prompt")
        .mockRejectedValue(new Error("private provider setup detail"));
      const events: PiRuntimeEvent[] = [];
      await expect(runtime.streamMessage(session.id, "legacy request", (event) => {
        events.push(event);
      })).rejects.toThrow("private provider setup detail");
      expect(prompt).toHaveBeenCalledOnce();
      expect(events.map((event) => event.type)).toEqual(["message.accepted", "message.start", "message.failed"]);
      expect(events[0]).toMatchObject({ clientMessageId: expect.stringMatching(/^[A-Za-z0-9._:-]+$/) });
      expect(JSON.stringify(events)).not.toContain("private provider setup detail");
      expect((await runtime.getSession(session.id)).messages[0]).toMatchObject({
        role: "user", text: "legacy request", status: "failed",
      });
      await expect(runtime.streamMessage(session.id, "invalid", () => {}, "bad id"))
        .rejects.toThrow("safe identifier");
      expect(prompt).toHaveBeenCalledOnce();
    } finally {
      await runtime.close();
    }
  });
});
