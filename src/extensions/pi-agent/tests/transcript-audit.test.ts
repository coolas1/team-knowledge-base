import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { describe, expect, it } from "vitest";
import { loadPiAgentConfig } from "../src/config.js";
import { auditTranscripts, verifyTranscriptRecovery } from "../src/transcript-audit.js";

describe("transcript audit", () => {
  it("reports a missing journal without returning message bodies", async () => {
    const root = await mkdtemp(path.join(tmpdir(), "tkb-audit-"));
    const sessionDir = path.join(root, "sessions");
    const manager = SessionManager.create(root, sessionDir);
    manager.appendMessage({ role: "user", content: "private question", timestamp: Date.now() });
    manager.appendMessage({
      role: "assistant", content: [{ type: "text", text: "private answer" }],
      api: "openai-completions", provider: "test", model: "test",
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      stopReason: "stop", timestamp: Date.now(),
    });
    const config = loadPiAgentConfig({
      PI_AGENT_CWD: root,
      PI_AGENT_DATA_DIR: root,
      PI_AGENT_SESSION_DIR: sessionDir,
      PI_AGENT_TRANSCRIPT_DIR: path.join(root, "transcripts"),
    });
    const result = await auditTranscripts(config);
    expect(result).toMatchObject({ sessions: 1, missingJournalSessions: 1, countMismatches: 1 });
    expect(JSON.stringify(result)).not.toContain("private");

    const sourceBefore = await readFile(manager.getSessionFile()!, "utf8");
    const verification = await verifyTranscriptRecovery(config);
    expect(verification).toMatchObject({
      sessions: 1,
      sdkVisibleMessages: 2,
      recoveredVisibleMessages: 2,
      recoveryErrors: 0,
      sourceFilesChanged: 0,
    });
    expect(await readFile(manager.getSessionFile()!, "utf8")).toBe(sourceBefore);
    expect(JSON.stringify(verification)).not.toContain("private");
  });
});
