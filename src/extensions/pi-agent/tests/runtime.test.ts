import { describe, expect, it } from "vitest";
import { extractCitations } from "../src/runtime.js";

describe("Pi runtime result handling", () => {
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
