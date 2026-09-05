import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { loadPiAgentConfig, type PiAgentConfig } from "./config.js";
import {
  foldTranscript,
  legacyEventsFromBranch,
  TranscriptStore,
  TRANSCRIPT_VERSION,
} from "./transcript.js";

export interface TranscriptAuditSummary {
  sessions: number;
  compactedSessions: number;
  sdkVisibleMessages: number;
  journalVisibleMessages: number;
  missingJournalSessions: number;
  countMismatches: number;
  degradedJournals: number;
}

export interface TranscriptRecoveryVerification {
  sessions: number;
  compactedSessions: number;
  sdkVisibleMessages: number;
  recoveredVisibleMessages: number;
  recoveryErrors: number;
  sourceFilesChanged: number;
}

async function digest(file: string): Promise<string> {
  return createHash("sha256").update(await readFile(file)).digest("hex");
}

export async function auditTranscripts(config: PiAgentConfig): Promise<TranscriptAuditSummary> {
  const store = new TranscriptStore(config.transcriptDir);
  const infos = await SessionManager.list(config.cwd, config.sessionDir);
  const summary: TranscriptAuditSummary = {
    sessions: infos.length,
    compactedSessions: 0,
    sdkVisibleMessages: 0,
    journalVisibleMessages: 0,
    missingJournalSessions: 0,
    countMismatches: 0,
    degradedJournals: 0,
  };
  for (const info of infos) {
    const manager = SessionManager.open(info.path, config.sessionDir, config.cwd);
    const branch = manager.getBranch();
    if (branch.some((entry) => entry.type === "compaction")) summary.compactedSessions++;
    const legacy = foldTranscript({
      header: {
        type: "transcript", version: TRANSCRIPT_VERSION,
        sessionId: info.id, createdAt: info.created.toISOString(),
      },
      events: legacyEventsFromBranch(info.id, branch),
    });
    summary.sdkVisibleMessages += legacy.messages.length;
    const journal = await store.snapshot(info.id);
    if (!journal) {
      summary.missingJournalSessions++;
      summary.countMismatches++;
      continue;
    }
    summary.journalVisibleMessages += journal.messages.length;
    if (journal.messages.length !== legacy.messages.length) summary.countMismatches++;
    if (journal.diagnostic) summary.degradedJournals++;
  }
  return summary;
}

export async function verifyTranscriptRecovery(
  config: PiAgentConfig,
): Promise<TranscriptRecoveryVerification> {
  const infos = await SessionManager.list(config.cwd, config.sessionDir);
  const sourceBefore = new Map<string, string>();
  for (const info of infos) sourceBefore.set(info.path, await digest(info.path));
  const temporary = await mkdtemp(path.join(tmpdir(), "tkb-transcript-recovery-"));
  const store = new TranscriptStore(temporary);
  const result: TranscriptRecoveryVerification = {
    sessions: infos.length,
    compactedSessions: 0,
    sdkVisibleMessages: 0,
    recoveredVisibleMessages: 0,
    recoveryErrors: 0,
    sourceFilesChanged: 0,
  };
  try {
    for (const info of infos) {
      try {
        const manager = SessionManager.open(info.path, config.sessionDir, config.cwd);
        const branch = manager.getBranch();
        if (branch.some((entry) => entry.type === "compaction")) result.compactedSessions++;
        const projected = foldTranscript({
          header: {
            type: "transcript", version: TRANSCRIPT_VERSION,
            sessionId: info.id, createdAt: info.created.toISOString(),
          },
          events: legacyEventsFromBranch(info.id, branch),
        });
        result.sdkVisibleMessages += projected.messages.length;
        const recovered = await store.recover(info.id, branch, info.created.toISOString());
        result.recoveredVisibleMessages += recovered.messages.length;
        if (recovered.messages.length !== projected.messages.length) result.recoveryErrors++;
      } catch {
        result.recoveryErrors++;
      }
    }
    for (const info of infos) {
      if (sourceBefore.get(info.path) !== await digest(info.path)) result.sourceFilesChanged++;
    }
    return result;
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (import.meta.url === invokedPath) {
  const operation = process.argv.includes("--verify-recovery")
    ? verifyTranscriptRecovery(loadPiAgentConfig())
    : auditTranscripts(loadPiAgentConfig());
  operation
    .then((summary) => process.stdout.write(`${JSON.stringify(summary)}\n`))
    .catch((error) => {
      process.stderr.write(`transcript audit failed: ${error instanceof Error ? error.name : "unknown"}\n`);
      process.exitCode = 1;
    });
}
