import { describe, expect, it } from "vitest";
import { confirmedPendingProposal } from "../src/runtime.js";
import type { TranscriptSnapshot } from "../src/transcript.js";

function snapshot(userText: string, expiresAt = "2099-01-01T00:00:00.000Z"): TranscriptSnapshot {
  return {
    sessionId: "session-1",
    createdAt: "2026-01-01T00:00:00.000Z",
    modifiedAt: "2026-01-01T00:00:00.000Z",
    messages: [],
    submissions: {},
    turns: [
      {
        id: "assistant-turn-1",
        clientMessageId: "client-1",
        userMessageId: "user-1",
        userText: "请给出方案",
        status: "completed",
        timestamp: "2026-01-01T00:00:00.000Z",
        assistantText: "建议以后排除第一条。",
        pendingProposal: {
          proposalType: "decision",
          normalizedContent: "后续查询排除第一条结果",
          assistantTurnId: "assistant-turn-1",
          trustedEvidenceIds: ["doc:1"],
          expiresAt,
        },
      },
      {
        id: "confirming-turn-2",
        clientMessageId: "client-2",
        userMessageId: "user-2",
        userText,
        status: "accepted",
        timestamp: "2026-01-01T00:01:00.000Z",
      },
    ],
  };
}

describe("structured pending proposals", () => {
  it("binds a bare confirmation to the immediately previous proposal", () => {
    expect(confirmedPendingProposal(snapshot("同意"), "confirming-turn-2", "同意")?.normalizedContent)
      .toBe("后续查询排除第一条结果");
  });

  it("accepts CJK confirmations that carry a subject pronoun", () => {
    // 中文里"同意/确认"极少单独成句，前面常常带主语（我/我们）。
    for (const text of ["我同意", "我确认", "我们同意", "好的我同意"]) {
      expect(
        confirmedPendingProposal(snapshot(text), "confirming-turn-2", text)
          ?.normalizedContent,
      ).toBe("后续查询排除第一条结果");
    }
  });

  it("still rejects negated confirmations", () => {
    for (const text of ["我不同意", "不确定", "没同意", "别同意"]) {
      expect(
        confirmedPendingProposal(snapshot(text), "confirming-turn-2", text),
      ).toBeUndefined();
    }
  });

  it("rejects negative, missing, and expired proposals", () => {
    expect(confirmedPendingProposal(snapshot("不同意"), "confirming-turn-2", "不同意"))
      .toBeUndefined();
    expect(confirmedPendingProposal({ ...snapshot("同意"), turns: [snapshot("同意").turns[1]] }, "confirming-turn-2", "同意"))
      .toBeUndefined();
    expect(confirmedPendingProposal(snapshot("同意", "2020-01-01T00:00:00.000Z"), "confirming-turn-2", "同意"))
      .toBeUndefined();
  });
});
