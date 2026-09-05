import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import {
  foldTranscript,
  legacyEventsFromBranch,
  TranscriptStore,
  MAX_TRANSCRIPT_RECORD_BYTES,
  TRANSCRIPT_VERSION,
  type TranscriptHeader,
  type TranscriptEvent,
} from "../src/transcript.js";

const header: TranscriptHeader = {
  type: "transcript", version: TRANSCRIPT_VERSION, sessionId: "s1", createdAt: "2026-01-01T00:00:00.000Z",
};

function accepted(turnId: string, clientMessageId = turnId): TranscriptEvent {
  return {
    type: "user.accepted", sessionId: "s1", turnId, messageId: `user-${turnId}`,
    clientMessageId, text: `question ${turnId}`, timestamp: "2026-01-01T00:00:01.000Z",
  };
}

describe("transcript journal", () => {
  it("folds every lifecycle state deterministically", () => {
    const statuses = ["accepted", "running", "completed", "failed", "cancelled", "interrupted"] as const;
    for (const status of statuses) {
      const events: TranscriptEvent[] = [accepted(status)];
      if (status !== "accepted") events.push({
        type: "turn.running", sessionId: "s1", turnId: status, timestamp: "2026-01-01T00:00:02.000Z",
      });
      if (status === "completed") events.push({
        type: "assistant.completed", sessionId: "s1", turnId: status,
        messageId: "answer", text: "answer", timestamp: "2026-01-01T00:00:03.000Z",
      });
      else if (status === "failed" || status === "cancelled" || status === "interrupted") events.push({
        type: "turn.terminal", sessionId: "s1", turnId: status, status,
        timestamp: "2026-01-01T00:00:03.000Z",
      });
      const snapshot = foldTranscript({ header, events });
      expect(snapshot.turns[0].status).toBe(status);
      expect(snapshot.messages[0].status).toBe(status);
      expect(snapshot.messages).toHaveLength(status === "completed" ? 2 : 1);
    }

    const completed: TranscriptEvent = {
      type: "assistant.completed", sessionId: "s1", turnId: "repeat",
      messageId: "answer-repeat", text: "first answer", timestamp: "2026-01-01T00:00:03.000Z",
    };
    const repeated = foldTranscript({ header, events: [accepted("repeat"), completed, completed] });
    expect(repeated.messages.map((message) => message.text)).toEqual(["question repeat", "first answer"]);
  });

  it("persists acceptance before returning and deduplicates retries", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    await store.initialize("s1");
    const first = await store.accept("s1", "durable question", "client-1");
    const onDisk = await readFile(path.join(directory, "s1.jsonl"), "utf8");
    expect(onDisk).toContain("durable question");
    const replay = await store.accept("s1", "durable question", "client-1");
    expect(replay.replayed).toBe(true);
    expect(replay.turn.id).toBe(first.turn.id);
    expect((await store.snapshot("s1"))?.messages).toHaveLength(1);
  });

  it("serializes concurrent acceptance for the same client id", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    await store.initialize("s1");
    const results = await Promise.all([
      store.accept("s1", "question", "client-1"),
      store.accept("s1", "question", "client-1"),
    ]);
    expect(new Set(results.map((result) => result.turn.id)).size).toBe(1);
    expect((await store.snapshot("s1"))?.messages).toHaveLength(1);
  });

  it("keeps a valid prefix and reports only a safe diagnostic for a torn tail", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    await store.initialize("s1");
    await store.accept("s1", "secret message", "client-1");
    await writeFile(path.join(directory, "s1.jsonl"), `${await readFile(path.join(directory, "s1.jsonl"), "utf8")}{private-token`, "utf8");
    const snapshot = await store.snapshot("s1");
    expect(snapshot?.messages[0].text).toBe("secret message");
    expect(snapshot?.diagnostic).toEqual({ code: "malformed_trailing_record" });
    expect(JSON.stringify(snapshot?.diagnostic)).not.toContain("secret");
    await expect(store.accept("s1", "later", "client-2")).rejects.toThrow("not writable");
  });

  it("rejects an oversized record before appending it", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    await store.initialize("s1");
    await expect(store.accept("s1", "x".repeat(MAX_TRANSCRIPT_RECORD_BYTES), "client-1"))
      .rejects.toThrow("too large");
    expect((await store.snapshot("s1"))?.messages).toHaveLength(0);
  });

  it("projects the full active branch while filtering internal entries", () => {
    const entries = [
      { type: "message", id: "system", timestamp: "2026-01-01T00:00:00Z", message: { role: "system", content: "private system" } },
      { type: "custom", id: "custom", customType: "private", data: { text: "private custom" } },
      { type: "custom_message", id: "memory", customType: "conversation-memory", content: "private memory", display: false },
      { type: "message", id: "u1", timestamp: "2026-01-01T00:00:00Z", message: { role: "user", content: [{ type: "text", text: "first" }] } },
      { type: "message", id: "a-tool", timestamp: "2026-01-01T00:00:01Z", message: { role: "assistant", content: [{ type: "toolCall", name: "search" }] } },
      { type: "message", id: "a-reasoning", timestamp: "2026-01-01T00:00:01Z", message: { role: "assistant", content: [{ type: "thinking", thinking: "private reasoning" }] } },
      { type: "message", id: "tool", timestamp: "2026-01-01T00:00:02Z", message: { role: "toolResult", content: [{ type: "text", text: "private" }] } },
      { type: "compaction", id: "compact", parentId: "tool", summary: "private summary" },
      { type: "message", id: "a1", timestamp: "2026-01-01T00:00:03Z", message: { role: "assistant", content: [{ type: "thinking", thinking: "private" }, { type: "text", text: "answer" }] } },
    ];
    const snapshot = foldTranscript({ header, events: legacyEventsFromBranch("s1", entries) });
    expect(snapshot.messages.map(({ role, text }) => ({ role, text }))).toEqual([
      { role: "user", text: "first" },
      { role: "assistant", text: "answer" },
    ]);
  });

  it("uses a real SDK active branch across compaction and excludes an abandoned branch", () => {
    const manager = SessionManager.inMemory("/test");
    const user = (text: string) => ({ role: "user" as const, content: text, timestamp: Date.now() });
    const assistant = (text: string) => ({
      role: "assistant" as const, content: [{ type: "text" as const, text }],
      api: "openai-completions" as const, provider: "test", model: "test",
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      stopReason: "stop" as const, timestamp: Date.now(),
    });
    const first = manager.appendMessage(user("first"));
    manager.appendMessage(assistant("first answer"));
    manager.appendCompaction("summary", first, 100);
    const abandoned = manager.appendMessage(user("abandoned"));
    manager.appendMessage(assistant("abandoned answer"));
    manager.branch(first);
    manager.appendCompaction("second summary", first, 100);
    manager.appendMessage(user("active"));
    manager.appendMessage(assistant("active answer"));
    expect(manager.getEntries().some((entry) => entry.id === abandoned)).toBe(true);

    const snapshot = foldTranscript({ header, events: legacyEventsFromBranch("s1", manager.getBranch()) });
    expect(snapshot.messages.map(message => message.text)).toEqual([
      "first", "active", "active answer",
    ]);
  });

  it("recovers once without changing the legacy source", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    const entries = [{ type: "message", id: "u1", timestamp: "2026-01-01T00:00:00Z", message: { role: "user", content: "question" } }];
    const source = JSON.stringify(entries);
    const [first, second] = await Promise.all([store.recover("s1", entries), store.recover("s1", entries)]);
    expect(first.messages).toHaveLength(1);
    expect(second.messages).toHaveLength(1);
    expect(JSON.stringify(entries)).toBe(source);
  });

  it("marks unfinished turns interrupted after restart", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "tkb-transcript-"));
    const store = new TranscriptStore(directory);
    await store.initialize("s1");
    await store.accept("s1", "question", "client-1");
    const snapshot = await store.interruptUnfinished("s1");
    expect(snapshot.turns[0].status).toBe("interrupted");
    expect(snapshot.messages[0].text).toBe("question");
    const retry = await store.accept("s1", "question", "client-2");
    expect(retry.replayed).toBe(false);
    expect((await store.snapshot("s1"))?.turns.map((turn) => turn.status)).toEqual([
      "interrupted", "accepted",
    ]);
  });
});
