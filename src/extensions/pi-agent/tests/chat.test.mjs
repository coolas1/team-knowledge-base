import { describe, expect, it } from "vitest";

import { parseSseFrames, renderEvent } from "../scripts/chat.mjs";

describe("parseSseFrames", () => {
  it("parses complete frames and returns an empty tail", () => {
    const buf =
      'event: tool.start\ndata: {"toolName":"tkb_search_fast"}\n\n' +
      'event: assistant.delta\ndata: {"delta":"hi"}\n\n';
    const { frames, rest } = parseSseFrames(buf);
    expect(frames).toHaveLength(2);
    expect(frames[0]).toEqual({
      type: "tool.start",
      data: { toolName: "tkb_search_fast" },
    });
    expect(frames[1]).toEqual({
      type: "assistant.delta",
      data: { delta: "hi" },
    });
    expect(rest).toBe("");
  });

  it("keeps an incomplete final frame as the tail", () => {
    const buf =
      'event: tool.start\ndata: {"toolName":"x"}\n\n' +
      "event: assistant.del";
    const { frames, rest } = parseSseFrames(buf);
    expect(frames).toHaveLength(1);
    expect(frames[0].type).toBe("tool.start");
    expect(rest).toBe("event: assistant.del");
  });

  it("ignores empty frames", () => {
    const { frames } = parseSseFrames("\n\nevent: x\ndata: {}\n\n");
    expect(frames).toHaveLength(1);
    expect(frames[0].type).toBe("x");
    expect(frames[0].data).toEqual({});
  });

  it("falls back to the raw string when data is not JSON", () => {
    const { frames } = parseSseFrames("event: odd\ndata: not-json\n\n");
    expect(frames).toHaveLength(1);
    expect(frames[0].type).toBe("odd");
    expect(frames[0].data).toBe("not-json");
  });
});

describe("renderEvent", () => {
  it("streams assistant deltas verbatim", () => {
    expect(renderEvent({ type: "assistant.delta", delta: "Hello" })).toEqual({
      text: "Hello",
      tone: "stream",
    });
  });

  it("streams thinking deltas under the think tone", () => {
    expect(renderEvent({ type: "assistant.thinking", delta: "hmm" })).toEqual({
      text: "hmm",
      tone: "think",
    });
  });

  it("formats tool.start", () => {
    expect(
      renderEvent({ type: "tool.start", toolName: "tkb_search_fast", args: {} }),
    ).toEqual({ text: "🔧 tkb_search_fast", tone: "info" });
  });

  it("formats a successful tool.result", () => {
    expect(
      renderEvent({
        type: "tool.result",
        toolName: "tkb_search_fast",
        isError: false,
      }),
    ).toEqual({ text: "  ✓ tkb_search_fast", tone: "ok" });
  });

  it("formats a failed tool.result", () => {
    expect(
      renderEvent({
        type: "tool.result",
        toolName: "tkb_search_fast",
        isError: true,
      }),
    ).toEqual({ text: "  ⚠ tkb_search_fast failed", tone: "error" });
  });

  it("formats limit.reached", () => {
    expect(
      renderEvent({ type: "limit.reached", limit: "tool_calls", maximum: 12 }),
    ).toEqual({
      text: "⚠ tool_calls limit reached (max 12)",
      tone: "warn",
    });
  });

  it("formats message.failed with whatever message field is present", () => {
    const out = renderEvent({ type: "message.failed", message: "boom" });
    expect(out.tone).toBe("error");
    expect(out.text).toContain("boom");
  });

  it("defers citation and lifecycle events to the loop", () => {
    expect(renderEvent({ type: "message.start", sessionId: "s" }).tone).toBe(
      "skip",
    );
    expect(
      renderEvent({ type: "citation", docId: "d1", title: "T" }).tone,
    ).toBe("skip");
    expect(
      renderEvent({
        type: "message.completed",
        sessionId: "s",
        answer: "",
        toolCalls: 0,
      }).tone,
    ).toBe("skip");
  });
});
