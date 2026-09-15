import { readFile } from "node:fs/promises";
import type { AddressInfo } from "node:net";
import { resolve } from "node:path";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import type { AgentRuntimeApi, PiRuntimeEvent } from "../src/runtime.js";
import { createPiAgentHttpServer } from "../src/server.js";

type ReplayCase = {
  id: string;
  prompt: string;
  route: "knowledge" | "conversation" | "mixed";
  document_ids: string[];
  conversation_ids: string[];
  citations: string[];
  answer: string;
  budget_ms: number;
};

const fixture = JSON.parse(
  await readFile(
    resolve(process.cwd(), "../../../benchmark/eval/retrieval-refine/replay_cases.json"),
    "utf8",
  ),
) as { cases: ReplayCase[] };
const byPrompt = new Map(fixture.cases.map((item) => [item.prompt, item]));

const runtime = {
  initialize: vi.fn(async () => undefined),
  health: vi.fn(async () => ({ status: "ok" as const })),
  createSession: vi.fn(async () => ({ id: "acceptance", messageCount: 0, streaming: false })),
  listSessions: vi.fn(async () => []),
  getSession: vi.fn(async () => ({ id: "acceptance", messageCount: 0, streaming: false, messages: [] })),
  streamMessage: vi.fn(async (sessionId: string, prompt: string, emit: (event: PiRuntimeEvent) => void) => {
    const item = byPrompt.get(prompt)!;
    emit({ type: "message.start", sessionId });
    emit({
      type: "tool.result",
      toolCallId: item.id,
      toolName: "tkb_query_knowledge",
      isError: false,
      result: {
        document_evidence: item.document_ids.map((doc_id) => ({ doc_id })),
        conversation_context: item.conversation_ids.map((memory_id) => ({ memory_id })),
      },
    });
    for (const docId of item.citations) {
      emit({ type: "citation", docId, title: docId.replace("doc-", "") });
    }
    emit({ type: "assistant.delta", delta: item.answer });
    emit({ type: "message.completed", sessionId, answer: item.answer, toolCalls: 1 });
  }),
  cancel: vi.fn(async () => true),
  deleteSession: vi.fn(async () => true),
  forgetSessionMemory: vi.fn(async () => ({ sessionId: "acceptance", cancelledJobs: 0, deletedDocuments: 0 })),
  close: vi.fn(async () => undefined),
} as unknown as AgentRuntimeApi;

const server = createPiAgentHttpServer(runtime);
let base = "";

beforeAll(async () => {
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((done) => server.close(() => done()));
});

it("replays retrieval acceptance cases through Pi HTTP/SSE within budget", async () => {
  for (const item of fixture.cases) {
    const started = performance.now();
    const response = await fetch(`${base}/v1/sessions/acceptance/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: item.prompt, clientMessageId: item.id }),
    });
    const stream = await response.text();
    expect(performance.now() - started).toBeLessThan(item.budget_ms);
    expect(stream).toContain("event: message.completed");
    expect(stream).toContain(JSON.stringify(item.answer).slice(1, -1));
    for (const docId of item.citations) expect(stream).toContain(`\"docId\":\"${docId}\"`);
    for (const turnId of item.conversation_ids) {
      expect(stream).toContain(`\"memory_id\":\"${turnId}\"`);
      expect(stream).not.toContain(`\"docId\":\"${turnId}\"`);
    }
    expect((stream.match(/event: citation/g) ?? []).length).toBe(item.citations.length);
  }
});
