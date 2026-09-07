import { describe, expect, it } from "vitest";
import {
  conversationMessagesFrom,
  extractCitations,
  sessionTitleFrom,
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
});
