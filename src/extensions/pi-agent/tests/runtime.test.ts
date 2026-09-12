import { describe, expect, it } from "vitest";
import {
  conversationMessagesFrom,
  extractCitations,
  sessionTitleFrom,
  terminalLengthFailureFrom,
  terminalPptFailureFrom,
} from "../src/runtime.js";

describe("Pi runtime result handling", () => {
  it("builds concise session titles from the first user message", () => {
    expect(sessionTitleFrom("请帮我总结一下本周项目进展？")).toBe("总结一下本周项目进展");
    expect(sessionTitleFrom("  # 比较第一季度和第二季度的销售表现、主要差异以及背后的原因  ")).toBe(
      "比较第一季度和第二季度的销售表现、主要差异以及背…",
    );
    expect(sessionTitleFrom("   ")).toBeUndefined();
  });

  it("exposes only visible user and assistant text from session history", () => {
    expect(
      conversationMessagesFrom([
        { role: "system", content: "private system prompt" },
        {
          role: "user",
          content: [
            { type: "text", text: "Compare the documents" },
            { type: "image", data: "private image data" },
          ],
        },
        {
          role: "assistant",
          content: [
            { type: "thinking", thinking: "private reasoning" },
            { type: "text", text: "The documents agree." },
            { type: "toolCall", name: "tkb_search_deep", arguments: {} },
          ],
        },
        { role: "toolResult", content: [{ type: "text", text: "raw tool result" }] },
        { role: "assistant", content: [{ type: "thinking", thinking: "only reasoning" }] },
      ]),
    ).toEqual([
      { role: "user", text: "Compare the documents" },
      { role: "assistant", text: "The documents agree." },
    ]);
  });

  it("extracts and deduplicates document citations from MCP payloads", () => {
    const citations = extractCitations({
      sources: [
        { doc_id: "a", title: "Week 1" },
        { doc_id: "a", title: "Week 1" },
      ],
      chunks: [{ doc_id: "b", doc_title: "Week 2" }],
    });
    expect(citations).toEqual([
      { docId: "a", title: "Week 1" },
      { docId: "b", title: "Week 2" },
    ]);
  });

  it("can extract citations from JSON text tool results", () => {
    expect(extractCitations('{"sources":[{"doc_id":"x","title":"Doc"}]}')).toEqual([
      { docId: "x", title: "Doc" },
    ]);
  });

  it("turns a terminal PPT tool failure into a completed user-visible answer", () => {
    expect(terminalPptFailureFrom({
      role: "toolResult",
      toolName: "tkb_generate_image_ppt",
      details: { errorSummary: "visual_check_failed: page 7" },
    })).toContain("PPT 生成失败：visual_check_failed: page 7");
    expect(terminalPptFailureFrom({
      role: "toolResult",
      toolName: "tkb_search_fast",
      details: { errorSummary: "unrelated" },
    })).toBeUndefined();

    expect(terminalPptFailureFrom({
      role: "toolResult",
      toolName: "tkb_generate_image_ppt",
      content: [{
        type: "text",
        text: "Tool call was not executed: response hit output token limit, arguments may be truncated",
      }],
    })).toContain("工具没有执行");
  });

  it("turns an empty length-limited model response into a completed answer", () => {
    expect(terminalLengthFailureFrom({
      role: "assistant",
      stopReason: "length",
      content: [{ type: "thinking", thinking: "hidden" }],
    })).toContain("模型输出达到长度上限");
    expect(terminalLengthFailureFrom({
      role: "assistant",
      stopReason: "stop",
      content: [],
    })).toBeUndefined();
  });
});
