import path from "node:path";
import type { TkbMcpClient } from "./mcp-client.js";
import { TranscriptStore, completedTurnDelivery, type TranscriptTurn } from "./transcript.js";

/** One serialized, bounded scanner per trusted namespace; no bearer tokens on disk. */
export class ConversationDeliveryWorker {
  private pending: Promise<void> = Promise.resolve();
  private timer?: ReturnType<typeof setInterval>;
  private closing = false;
  private scheduled = false;
  private readonly archive: TranscriptStore;

  constructor(
    private readonly store: TranscriptStore,
    private readonly client: TkbMcpClient,
    private readonly scopeKey: string,
    private readonly options = { pollMs: 5000, maxAttempts: 10, batchSize: 20, timeoutMs: 10000 },
  ) { this.archive = new TranscriptStore(path.join(store.directory, ".delivery")); }

  start(): void {
    const schedule = () => {
      if (this.scheduled || this.closing) return;
      this.scheduled = true;
      void this.run().catch(() => undefined).finally(() => { this.scheduled = false; });
    };
    this.timer = setInterval(schedule, this.options.pollMs);
    this.timer.unref();
    schedule();
  }

  async close(): Promise<void> {
    this.closing = true;
    if (this.timer) clearInterval(this.timer);
    await this.pending;
  }

  private serialized(operation: () => Promise<void>): Promise<void> {
    const task = this.pending.then(operation);
    this.pending = task.catch(() => undefined);
    return task;
  }

  run(sessionId?: string): Promise<void> {
    return this.serialized(async () => {
      if (this.closing) return;
      let remaining = this.options.batchSize;
      for (const store of [this.store, this.archive]) {
        const ids = sessionId ? [sessionId] : await store.listSessionIds();
        for (const id of ids) {
          const snapshot = await store.snapshot(id);
          if (!snapshot || snapshot.diagnostic) continue;
          for (const turn of snapshot.turns) {
            if (!this.isPending(turn) || (turn.deliveryResult?.nextAttemptAt
              && Date.parse(turn.deliveryResult.nextAttemptAt) > Date.now())) continue;
            if (remaining-- <= 0 || this.closing) return;
            await this.deliver(store, id, turn);
          }
          if (store === this.archive) {
            const latest = await store.snapshot(id);
            if (latest?.turns.every((turn) => ["accepted", "cancelled"].includes(turn.deliveryResult?.status ?? ""))) {
              await store.delete(id);
            }
          }
        }
      }
    });
  }

  async status(): Promise<Record<string, number>> {
    const counts = { pending: 0, accepted: 0, failed: 0, conflict: 0, cancelled: 0, invalid: 0 };
    const seen = new Set<string>();
    for (const store of [this.store, this.archive]) {
      for (const id of await store.listSessionIds()) {
        const snapshot = await store.snapshot(id);
        if (snapshot?.diagnostic) { counts.invalid++; continue; }
        for (const turn of snapshot?.turns ?? []) {
          if (!turn.delivery || seen.has(turn.delivery.key)) continue;
          seen.add(turn.delivery.key);
          counts[turn.deliveryResult?.status ?? "pending"]++;
        }
      }
    }
    return counts;
  }

  private isPending(turn: TranscriptTurn): boolean {
    return turn.status === "completed" && Boolean(turn.delivery) &&
      (!turn.deliveryResult || turn.deliveryResult.status === "pending");
  }

  private async deliver(store: TranscriptStore, sessionId: string, turn: TranscriptTurn): Promise<void> {
    const attempts = (turn.deliveryResult?.attempts ?? 0) + 1;
    let status: "accepted" | "pending" | "conflict" | "failed" = "pending";
    let errorCode: string | undefined;
    let operationId: string | undefined;
    try {
      const expected = completedTurnDelivery(this.scopeKey, sessionId, turn.id, turn.userText, turn.assistantText!);
      if (turn.delivery!.scopeKey !== this.scopeKey || expected.key !== turn.delivery!.key
        || expected.contentHash !== turn.delivery!.contentHash) throw new Error("delivery_scope_or_content_conflict");
      const result = await this.client.enqueueConversationTurn({
        sessionId, turnId: turn.id, userText: turn.userText, assistantText: turn.assistantText!,
        requireDurableAcceptance: true,
        sourceTimestamp: turn.timestamp,
        referenceTimezone: "UTC",
      }, { timeoutMs: this.options.timeoutMs });
      if (result.durable_acceptance !== true || result.content_hash !== expected.contentHash) {
        throw new Error("delivery_acknowledgement_invalid");
      }
      status = "accepted";
      operationId = result.operation_id;
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      const conflict = message.includes("conversation_delivery_conflict") || message === "delivery_scope_or_content_conflict";
      status = conflict ? "conflict" : attempts >= this.options.maxAttempts ? "failed" : "pending";
      errorCode = conflict ? "content_conflict" : "delivery_unconfirmed";
    }
    // If persisting this result fails, the original intent remains pending and
    // is safely replayed with the same identity after recovery.
    await store.append({ type: "delivery.result", sessionId, turnId: turn.id,
      timestamp: new Date().toISOString(), status, attempts, errorCode, operationId,
      ...(status === "pending" ? { nextAttemptAt: new Date(Date.now()
        + Math.min(300000, 1000 * 2 ** Math.min(attempts, 8))).toISOString() } : {}),
    });
  }

  /** Preserve undelivered completed turns before deleting visible history. */
  deleteHistory(sessionId: string, remove: () => Promise<void>): Promise<void> {
    return this.serialized(async () => {
      const snapshot = await this.store.snapshot(sessionId);
      if (snapshot?.turns.some((turn) => turn.delivery && turn.deliveryResult?.status !== "accepted"
        && turn.deliveryResult?.status !== "cancelled")) {
        if (snapshot.diagnostic) throw new Error("cannot archive an invalid delivery journal");
        // A synced copy precedes deletion. A crash between them only duplicates
        // an idempotent submission, never loses the sole durable intent.
        await this.store.archiveDelivery(sessionId, this.archive);
      }
      await remove();
    });
  }

  cancelPending(sessionId: string): Promise<void> {
    return this.serialized(async () => {
      for (const store of [this.store, this.archive]) {
        const snapshot = await store.snapshot(sessionId);
        for (const turn of snapshot?.turns ?? []) {
          if (!turn.delivery || turn.deliveryResult?.status === "accepted") continue;
          await store.append({ type: "delivery.result", sessionId, turnId: turn.id,
            timestamp: new Date().toISOString(), status: "cancelled", attempts: turn.deliveryResult?.attempts ?? 0 });
        }
      }
    });
  }
}
