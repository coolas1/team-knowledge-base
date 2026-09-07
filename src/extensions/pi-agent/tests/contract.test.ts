import { describe, expect, it } from "vitest";
import {
  CONVERSATION_MEMORY_MCP_CONTRACT,
  ENGINE_MCP_CONTRACT,
  inspectEngineContract,
} from "../src/contract.js";

function completeTools() {
  return Object.entries({
    ...ENGINE_MCP_CONTRACT,
    ...CONVERSATION_MEMORY_MCP_CONTRACT,
  }).map(([name, contract]) => ({
    name,
    description: "",
    inputSchema: {
      properties: Object.fromEntries(contract.required.map((key) => [key, { type: "string" }])),
      required: contract.required,
    },
  }));
}

describe("inspectEngineContract", () => {
  it("accepts the current eleven-tool engine contract", () => {
    expect(inspectEngineContract(completeTools()).ok).toBe(true);
  });

  it("requires internal memory operations only when memory is enabled", () => {
    const withoutMemory = completeTools().filter(
      (tool) => !(tool.name in CONVERSATION_MEMORY_MCP_CONTRACT),
    );
    expect(inspectEngineContract(withoutMemory).ok).toBe(true);
    const report = inspectEngineContract(withoutMemory, true);
    expect(report.ok).toBe(false);
    expect(report.missingTools).toContain("enqueue_conversation_turn");
  });

  it("reports missing tools and required parameters", () => {
    const tools = completeTools().filter((tool) => tool.name !== "query_knowledge");
    const fast = tools.find((tool) => tool.name === "search_knowledge_fast")!;
    fast.inputSchema.required = [];
    const report = inspectEngineContract(tools);
    expect(report.ok).toBe(false);
    expect(report.missingTools).toContain("query_knowledge");
    expect(report.schemaErrors).toContain("search_knowledge_fast.query must be required");
  });
});
