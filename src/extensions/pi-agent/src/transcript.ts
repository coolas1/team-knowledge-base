import { randomUUID } from "node:crypto";
import { mkdir, open, readFile, readdir, rename, rm, stat } from "node:fs/promises";
import path from "node:path";

export const TRANSCRIPT_VERSION = 1;
export const MAX_TRANSCRIPT_RECORD_BYTES = 1_048_576;

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

export type TurnStatus =
  | "accepted"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface TranscriptHeader {
  type: "transcript";
  version: typeof TRANSCRIPT_VERSION;
  sessionId: string;
  createdAt: string;
}

export interface UserAcceptedEvent {
  type: "user.accepted";
  sessionId: string;
  turnId: string;
  messageId: string;
  clientMessageId: string;
  text: string;
  timestamp: string;
}

export interface TurnRunningEvent {
  type: "turn.running";
  sessionId: string;
  turnId: string;
  timestamp: string;
}

export interface AssistantCompletedEvent {
  type: "assistant.completed";
  sessionId: string;
  turnId: string;
  messageId: string;
  text: string;
  timestamp: string;
}

export interface TurnTerminalEvent {
  type: "turn.terminal";
  sessionId: string;
  turnId: string;
  status: "failed" | "cancelled" | "interrupted";
  errorCode?: string;
  timestamp: string;
}

export type TranscriptEvent =
  | UserAcceptedEvent
  | TurnRunningEvent
  | AssistantCompletedEvent
  | TurnTerminalEvent;

export interface TranscriptMessage {
  id: string;
  turnId: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
  status: TurnStatus;
  clientMessageId?: string;
}

export interface TranscriptTurn {
  id: string;
  clientMessageId: string;
  userMessageId: string;
  userText: string;
  status: TurnStatus;
  timestamp: string;
  assistantText?: string;
}

export interface TranscriptDiagnostic {
  code: "invalid_header" | "malformed_trailing_record" | "record_too_large";
}

export interface TranscriptSnapshot {
  sessionId: string;
  createdAt: string;
  modifiedAt: string;
  messages: TranscriptMessage[];
  turns: TranscriptTurn[];
  submissions: Record<string, string>;
  diagnostic?: TranscriptDiagnostic;
}

interface ParsedJournal {
  header: TranscriptHeader;
  events: TranscriptEvent[];
  diagnostic?: TranscriptDiagnostic;
}

export interface AcceptedTurn {
  turn: TranscriptTurn;
  replayed: boolean;
}

export function assertSafeTranscriptId(value: string, label: string): void {
  if (!SAFE_ID.test(value)) throw new Error(`${label} must be a safe identifier`);
}

function isTerminal(status: TurnStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled" || status === "interrupted";
}

function requireNever(value: never): never {
  throw new Error(`unsupported transcript event: ${String(value)}`);
}

export function foldTranscript(parsed: ParsedJournal): TranscriptSnapshot {
  const turns = new Map<string, TranscriptTurn>();
  const messages: TranscriptMessage[] = [];
  const messageIds = new Set<string>();
  const submissions: Record<string, string> = {};
  let modifiedAt = parsed.header.createdAt;

  for (const event of parsed.events) {
    modifiedAt = event.timestamp > modifiedAt ? event.timestamp : modifiedAt;
    switch (event.type) {
      case "user.accepted": {
        if (submissions[event.clientMessageId] || turns.has(event.turnId)) break;
        const turn: TranscriptTurn = {
          id: event.turnId,
          clientMessageId: event.clientMessageId,
          userMessageId: event.messageId,
          userText: event.text,
          status: "accepted",
          timestamp: event.timestamp,
        };
        turns.set(event.turnId, turn);
        submissions[event.clientMessageId] = event.turnId;
        if (!messageIds.has(event.messageId)) {
          messages.push({
            id: event.messageId,
            turnId: event.turnId,
            clientMessageId: event.clientMessageId,
            role: "user",
            text: event.text,
            timestamp: event.timestamp,
            status: "accepted",
          });
          messageIds.add(event.messageId);
        }
        break;
      }
      case "turn.running": {
        const turn = turns.get(event.turnId);
        if (turn && !isTerminal(turn.status)) turn.status = "running";
        break;
      }
      case "assistant.completed": {
        const turn = turns.get(event.turnId);
        if (!turn || ["failed", "cancelled", "interrupted"].includes(turn.status)) break;
        turn.status = "completed";
        turn.assistantText = event.text;
        if (event.text.trim() && !messageIds.has(event.messageId)) {
          messages.push({
            id: event.messageId,
            turnId: event.turnId,
            role: "assistant",
            text: event.text,
            timestamp: event.timestamp,
            status: "completed",
          });
          messageIds.add(event.messageId);
        }
        break;
      }
      case "turn.terminal": {
        const turn = turns.get(event.turnId);
        if (turn && turn.status !== "completed") turn.status = event.status;
        break;
      }
      default:
        requireNever(event);
    }
  }

  for (const message of messages) {
    const turn = turns.get(message.turnId);
    if (turn) message.status = turn.status;
  }
  return {
    sessionId: parsed.header.sessionId,
    createdAt: parsed.header.createdAt,
    modifiedAt,
    messages,
    turns: [...turns.values()],
    submissions,
    diagnostic: parsed.diagnostic,
  };
}

function textFromMessage(message: unknown): string {
  if (!message || typeof message !== "object") return "";
  const content = (message as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part): part is { type: "text"; text: string } =>
      Boolean(part) && typeof part === "object" &&
      (part as { type?: unknown }).type === "text" &&
      typeof (part as { text?: unknown }).text === "string")
    .map((part) => part.text)
    .join("\n");
}

export function legacyEventsFromBranch(sessionId: string, entries: readonly unknown[]): TranscriptEvent[] {
  const events: TranscriptEvent[] = [];
  let activeTurn: { id: string; hasAssistant: boolean } | undefined;
  for (const value of entries) {
    if (!value || typeof value !== "object") continue;
    const entry = value as { type?: unknown; id?: unknown; timestamp?: unknown; message?: unknown };
    if (entry.type !== "message" || typeof entry.id !== "string" || !entry.message || typeof entry.message !== "object") continue;
    const message = entry.message as { role?: unknown; stopReason?: unknown; timestamp?: unknown };
    const role = message.role;
    if (role !== "user" && role !== "assistant") continue;
    if (role === "assistant" && message.stopReason === "error") continue;
    const text = textFromMessage(message).trim();
    if (!text) continue;
    const timestamp = typeof entry.timestamp === "string"
      ? entry.timestamp
      : new Date(typeof message.timestamp === "number" ? message.timestamp : 0).toISOString();
    if (role === "user") {
      if (activeTurn && !activeTurn.hasAssistant) {
        events.push({
          type: "turn.terminal", sessionId, turnId: activeTurn.id,
          status: "interrupted", timestamp,
        });
      }
      const turnId = `legacy-${entry.id}`;
      events.push({
        type: "user.accepted",
        sessionId,
        turnId,
        messageId: entry.id,
        clientMessageId: `legacy-${entry.id}`,
        text,
        timestamp,
      });
      activeTurn = { id: turnId, hasAssistant: false };
    } else if (activeTurn) {
      events.push({
        type: "assistant.completed",
        sessionId,
        turnId: activeTurn.id,
        messageId: entry.id,
        text,
        timestamp,
      });
      activeTurn.hasAssistant = true;
    }
  }
  if (activeTurn && !activeTurn.hasAssistant) {
    events.push({
      type: "turn.terminal", sessionId, turnId: activeTurn.id,
      status: "interrupted", timestamp: new Date().toISOString(),
    });
  }
  return events;
}

export class TranscriptStore {
  private readonly queues = new Map<string, Promise<void>>();

  constructor(readonly directory: string) {}

  private filePath(sessionId: string): string {
    assertSafeTranscriptId(sessionId, "sessionId");
    return path.join(this.directory, `${sessionId}.jsonl`);
  }

  private async locked<T>(sessionId: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.queues.get(sessionId) ?? Promise.resolve();
    let release!: () => void;
    const current = new Promise<void>((resolve) => { release = resolve; });
    const queued = previous.then(() => current);
    this.queues.set(sessionId, queued);
    await previous;
    try {
      return await operation();
    } finally {
      release();
      if (this.queues.get(sessionId) === queued) this.queues.delete(sessionId);
    }
  }

  private async parse(sessionId: string): Promise<ParsedJournal | undefined> {
    let content: string;
    try {
      content = await readFile(this.filePath(sessionId), "utf8");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw error;
    }
    const lines = content.split("\n");
    const records: unknown[] = [];
    let diagnostic: TranscriptDiagnostic | undefined;
    for (const line of lines) {
      if (!line.trim()) continue;
      if (Buffer.byteLength(line) > MAX_TRANSCRIPT_RECORD_BYTES) {
        diagnostic = { code: "record_too_large" };
        break;
      }
      try {
        records.push(JSON.parse(line));
      } catch {
        diagnostic = { code: "malformed_trailing_record" };
        break;
      }
    }
    const header = records[0] as Partial<TranscriptHeader> | undefined;
    if (header?.type !== "transcript" || header.version !== TRANSCRIPT_VERSION || header.sessionId !== sessionId || typeof header.createdAt !== "string") {
      const fallback: TranscriptHeader = {
        type: "transcript", version: TRANSCRIPT_VERSION, sessionId, createdAt: new Date(0).toISOString(),
      };
      return { header: fallback, events: [], diagnostic: { code: "invalid_header" } };
    }
    return { header: header as TranscriptHeader, events: records.slice(1) as TranscriptEvent[], diagnostic };
  }

  private async writeNew(sessionId: string, events: readonly TranscriptEvent[], createdAt: string): Promise<void> {
    await mkdir(this.directory, { recursive: true });
    const destination = this.filePath(sessionId);
    const temporary = `${destination}.${randomUUID()}.tmp`;
    const records: Array<TranscriptHeader | TranscriptEvent> = [
      { type: "transcript", version: TRANSCRIPT_VERSION, sessionId, createdAt },
      ...events,
    ];
    const lines = records.map((record) => JSON.stringify(record));
    if (lines.some((line) => Buffer.byteLength(line) > MAX_TRANSCRIPT_RECORD_BYTES)) {
      throw new Error("transcript record is too large");
    }
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(`${lines.join("\n")}\n`);
      await handle.sync();
    } finally {
      await handle.close();
    }
    try {
      await rename(temporary, destination);
    } catch (error) {
      await rm(temporary, { force: true });
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
  }

  private async appendUnlocked(sessionId: string, events: readonly TranscriptEvent[]): Promise<void> {
    if (!events.length) return;
    const lines = events.map((event) => JSON.stringify(event));
    if (lines.some((line) => Buffer.byteLength(line) > MAX_TRANSCRIPT_RECORD_BYTES)) {
      throw new Error("transcript record is too large");
    }
    const handle = await open(this.filePath(sessionId), "a", 0o600);
    try {
      await handle.writeFile(`${lines.join("\n")}\n`);
      await handle.sync();
    } finally {
      await handle.close();
    }
  }

  async initialize(sessionId: string, createdAt = new Date().toISOString()): Promise<TranscriptSnapshot> {
    return this.locked(sessionId, async () => {
      const existing = await this.parse(sessionId);
      if (existing) return foldTranscript(existing);
      await this.writeNew(sessionId, [], createdAt);
      return foldTranscript((await this.parse(sessionId))!);
    });
  }

  async recover(sessionId: string, entries: readonly unknown[], createdAt?: string): Promise<TranscriptSnapshot> {
    return this.locked(sessionId, async () => {
      const existing = await this.parse(sessionId);
      if (existing) return foldTranscript(existing);
      const events = legacyEventsFromBranch(sessionId, entries);
      const firstTimestamp = events[0]?.timestamp ?? createdAt ?? new Date().toISOString();
      await this.writeNew(sessionId, events, firstTimestamp);
      return foldTranscript((await this.parse(sessionId))!);
    });
  }

  async snapshot(sessionId: string): Promise<TranscriptSnapshot | undefined> {
    const parsed = await this.parse(sessionId);
    return parsed ? foldTranscript(parsed) : undefined;
  }

  async accept(sessionId: string, text: string, clientMessageId: string): Promise<AcceptedTurn> {
    assertSafeTranscriptId(clientMessageId, "clientMessageId");
    return this.locked(sessionId, async () => {
      let parsed = await this.parse(sessionId);
      if (!parsed) {
        await this.writeNew(sessionId, [], new Date().toISOString());
        parsed = (await this.parse(sessionId))!;
      }
      if (parsed.diagnostic) throw new Error(`transcript is not writable: ${parsed.diagnostic.code}`);
      const snapshot = foldTranscript(parsed);
      const existingTurnId = snapshot.submissions[clientMessageId];
      if (existingTurnId) {
        return { turn: snapshot.turns.find((turn) => turn.id === existingTurnId)!, replayed: true };
      }
      const timestamp = new Date().toISOString();
      const event: UserAcceptedEvent = {
        type: "user.accepted", sessionId, turnId: randomUUID(), messageId: randomUUID(),
        clientMessageId, text, timestamp,
      };
      await this.appendUnlocked(sessionId, [event]);
      return {
        replayed: false,
        turn: {
          id: event.turnId, clientMessageId, userMessageId: event.messageId,
          userText: text, status: "accepted", timestamp,
        },
      };
    });
  }

  async append(event: TranscriptEvent): Promise<void> {
    await this.locked(event.sessionId, async () => {
      const parsed = await this.parse(event.sessionId);
      if (!parsed) throw new Error("transcript is not initialized");
      if (parsed.diagnostic) throw new Error(`transcript is not writable: ${parsed.diagnostic.code}`);
      await this.appendUnlocked(event.sessionId, [event]);
    });
  }

  async interruptUnfinished(sessionId: string): Promise<TranscriptSnapshot> {
    return this.locked(sessionId, async () => {
      const parsed = await this.parse(sessionId);
      if (!parsed) throw new Error("transcript is not initialized");
      const snapshot = foldTranscript(parsed);
      if (parsed.diagnostic) return snapshot;
      const timestamp = new Date().toISOString();
      const events: TurnTerminalEvent[] = snapshot.turns
        .filter((turn) => turn.status === "accepted" || turn.status === "running")
        .map((turn) => ({
          type: "turn.terminal", sessionId, turnId: turn.id,
          status: "interrupted", errorCode: "process_interrupted", timestamp,
        }));
      await this.appendUnlocked(sessionId, events);
      return foldTranscript({ ...parsed, events: [...parsed.events, ...events] });
    });
  }

  async listSessionIds(): Promise<string[]> {
    try {
      return (await readdir(this.directory))
        .filter((name) => name.endsWith(".jsonl"))
        .map((name) => name.slice(0, -6))
        .filter((id) => SAFE_ID.test(id));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
  }

  async delete(sessionId: string): Promise<void> {
    await this.locked(sessionId, async () => {
      await rm(this.filePath(sessionId), { force: true });
    });
  }

  async fileMetadata(sessionId: string): Promise<{ size: number; modifiedAt: string } | undefined> {
    try {
      const value = await stat(this.filePath(sessionId));
      return { size: value.size, modifiedAt: value.mtime.toISOString() };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw error;
    }
  }
}
