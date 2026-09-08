import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { TranscriptStore, completedTurnDelivery } from "../src/transcript.js";
import { ConversationDeliveryWorker } from "../src/delivery.js";
import type { TkbMcpClient } from "../src/mcp-client.js";

afterEach(() => vi.restoreAllMocks());

async function fixture() {
  const directory = await mkdtemp(path.join(tmpdir(), "tkb-delivery-"));
  const store = new TranscriptStore(directory);
  await store.initialize("s1");
  const { turn } = await store.accept("s1", "问题", "request-1");
  const intent = completedTurnDelivery("scope-A", "s1", turn.id, "问题", "answer");
  await store.append({ type: "assistant.completed", sessionId: "s1", turnId: turn.id,
    messageId: "m1", text: "answer", timestamp: new Date().toISOString(), delivery: intent });
  const enqueue = vi.fn(async () => ({ document_id: "d1", status: "pending", durable_acceptance: true,
    content_hash: intent.contentHash }));
  const client = { enqueueConversationTurn: enqueue } as unknown as TkbMcpClient;
  const worker = () => new ConversationDeliveryWorker(new TranscriptStore(directory), client, "scope-A",
    { pollMs: 5000, batchSize: 20, maxAttempts: 2, timeoutMs: 1000 });
  return { store, turn, intent, enqueue, client, worker };
}

it("replays accepted remote delivery after a crash before its local acknowledgement", async () => {
  const { store, worker, enqueue, intent } = await fixture();
  const append = vi.spyOn(TranscriptStore.prototype, "append");
  append.mockRejectedValueOnce(new Error("disk unavailable"));
  await expect(worker().run()).rejects.toThrow("disk unavailable");
  expect((await store.snapshot("s1"))?.turns[0].deliveryResult).toBeUndefined();
  append.mockRestore();
  const recovered = worker();
  await recovered.run();
  expect(enqueue).toHaveBeenCalledTimes(2);
  expect(enqueue.mock.calls[0]).toEqual(enqueue.mock.calls[1]);
  expect((await store.snapshot("s1"))?.turns[0].delivery?.contentHash).toBe(intent.contentHash);
  expect((await recovered.status()).accepted).toBe(1);
  await recovered.run();
  expect(enqueue).toHaveBeenCalledTimes(2);
});

it("retains retry intent across history deletion and keeps incomplete turns out", async () => {
  const { store, worker, enqueue } = await fixture();
  await store.accept("s1", "incomplete", "request-2");
  const first = worker();
  await first.deleteHistory("s1", () => store.delete("s1"));
  expect(await store.snapshot("s1")).toBeUndefined();
  const archived = new TranscriptStore(path.join(store.directory, ".delivery"));
  expect((await archived.snapshot("s1"))?.turns).toHaveLength(1);
  const recovered = worker();
  await recovered.run();
  expect(enqueue).toHaveBeenCalledOnce();
  expect(await archived.snapshot("s1")).toBeUndefined();
});

it("requires explicit matching acknowledgement, backs off and stops at its attempt limit", async () => {
  const { store, worker, enqueue } = await fixture();
  enqueue.mockResolvedValue({ document_id: "d1", status: "pending", durable_acceptance: false, content_hash: "wrong" });
  const runner = worker();
  await runner.run();
  expect((await runner.status()).pending).toBe(1);
  await runner.run();
  expect(enqueue).toHaveBeenCalledOnce();
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now + 60000);
  await worker().run();
  expect((await store.snapshot("s1"))?.turns[0].deliveryResult).toMatchObject({ status: "failed", attempts: 2 });
  await worker().run();
  expect(enqueue).toHaveBeenCalledTimes(2);
});

it("rejects wrong scope and content conflicts without retrying or leaking raw errors", async () => {
  const { store, client, worker, enqueue } = await fixture();
  const wrong = new ConversationDeliveryWorker(store, client, "scope-B");
  await wrong.run();
  expect(enqueue).not.toHaveBeenCalled();
  expect((await worker().status()).conflict).toBe(1);
  const other = await fixture();
  other.enqueue.mockRejectedValue(new Error("conversation_delivery_conflict: private details"));
  await other.worker().run();
  const result = (await other.store.snapshot("s1"))?.turns[0].deliveryResult;
  expect(result).toMatchObject({ status: "conflict", errorCode: "content_conflict" });
  expect(JSON.stringify(result)).not.toContain("private details");
});

it("cancels durable pending work before explicit forgetting", async () => {
  const { worker, enqueue } = await fixture();
  await worker().cancelPending("s1");
  const restarted = worker();
  await restarted.run();
  expect(enqueue).not.toHaveBeenCalled();
  expect((await restarted.status()).cancelled).toBe(1);
});
