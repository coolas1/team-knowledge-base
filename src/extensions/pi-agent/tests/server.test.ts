import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  AgentRuntimeApi,
  PiRuntimeEvent,
  RuntimeHealth,
  RuntimeSessionDetail,
  RuntimeSessionInfo,
} from "../src/runtime.js";
import { createPiAgentHttpServer } from "../src/server.js";

const servers: ReturnType<typeof createPiAgentHttpServer>[] = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) => new Promise<void>((resolve) => server.close(() => resolve())),
    ),
  );
});

function fakeRuntime(): AgentRuntimeApi {
  const session: RuntimeSessionInfo = { id: "s1", messageCount: 0, streaming: false };
  const sessionDetail: RuntimeSessionDetail = {
    ...session,
    messages: [
      { role: "user", text: "question" },
      { role: "assistant", text: "answer" },
    ],
  };
  const health: RuntimeHealth = {
    status: "ok",
    model: { provider: "ollama", id: "qwen3:14b", baseUrl: "http://ollama/v1" },
    mcp: { ok: true, missingTools: [], schemaErrors: [], availableTools: [] },
    loadedSessions: 1,
  };
  return {
    initialize: vi.fn(async () => undefined),
    health: vi.fn(async () => health),
    createSession: vi.fn(async () => session),
    listSessions: vi.fn(async () => [session]),
    getSession: vi.fn(async () => sessionDetail),
    streamMessage: vi.fn(async (_id, _message, emit) => {
      await emit({ type: "message.start", sessionId: "s1", name: "知识库文件数量" });
      await emit({ type: "assistant.delta", delta: "hello" });
      await emit({ type: "message.completed", sessionId: "s1", answer: "hello", toolCalls: 0 });
    }),
    cancel: vi.fn(async () => true),
    deleteSession: vi.fn(async () => true),
    close: vi.fn(async () => undefined),
  };
}

async function listen(runtime: AgentRuntimeApi) {
  const server = createPiAgentHttpServer(runtime);
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as AddressInfo).port;
  return { runtime, base: `http://127.0.0.1:${port}` };
}

describe("Pi Agent HTTP service", () => {
  it("reports health and creates sessions", async () => {
    const { base } = await listen(fakeRuntime());
    const health = await fetch(`${base}/health`);
    expect(health.status).toBe(200);
    expect((await health.json()).status).toBe("ok");

    const created = await fetch(`${base}/v1/sessions`, { method: "POST" });
    expect(created.status).toBe(201);
    expect((await created.json()).id).toBe("s1");
  });

  it("returns message history only from the session detail endpoint", async () => {
    const { base } = await listen(fakeRuntime());

    const list = await fetch(`${base}/v1/sessions`);
    const listed = await list.json();
    expect(listed.items[0]).not.toHaveProperty("messages");

    const detail = await fetch(`${base}/v1/sessions/s1`);
    expect(detail.status).toBe(200);
    expect((await detail.json()).messages).toEqual([
      { role: "user", text: "question" },
      { role: "assistant", text: "answer" },
    ]);
  });

  it("streams typed SSE events for a user message", async () => {
    const runtime = fakeRuntime();
    const { base } = await listen(runtime);
    const response = await fetch(`${base}/v1/sessions/s1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "question" }),
    });
    const body = await response.text();
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(body).toContain("event: assistant.delta");
    expect(body).toContain('"name":"知识库文件数量"');
    expect(body).toContain('"delta":"hello"');
    expect(runtime.streamMessage).toHaveBeenCalledWith(
      "s1",
      "question",
      expect.any(Function),
    );
  });

  it("rejects an empty message before opening SSE", async () => {
    const { base } = await listen(fakeRuntime());
    const response = await fetch(`${base}/v1/sessions/s1/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "" }),
    });
    expect(response.status).toBe(400);
    expect((await response.json()).error).toContain("non-empty");
  });
});
