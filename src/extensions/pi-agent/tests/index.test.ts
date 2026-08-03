import { describe, expect, it, vi } from "vitest";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import tkbPiAgentAdapter from "../src/index.js";

describe("TKB Pi extension entry point", () => {
  it("registers the safe tools and startup validation hook", () => {
    const names: string[] = [];
    const events: string[] = [];
    const pi = {
      registerTool: vi.fn((tool: { name: string }) => names.push(tool.name)),
      on: vi.fn((event: string) => events.push(event)),
    } as unknown as ExtensionAPI;

    tkbPiAgentAdapter(pi);

    expect(names).toEqual([
      "tkb_query_knowledge",
      "tkb_search_fast",
      "tkb_search_deep",
      "tkb_get_document",
      "tkb_query_graph",
      "tkb_list_documents",
    ]);
    expect(events).toContain("session_start");
  });
});
