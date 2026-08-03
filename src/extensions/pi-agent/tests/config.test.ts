import { describe, expect, it } from "vitest";
import { loadTkbAdapterConfig } from "../src/config.js";

describe("loadTkbAdapterConfig", () => {
  it("uses safe defaults", () => {
    const config = loadTkbAdapterConfig({});
    expect(config.mcpUrl).toBe("http://localhost:8000/mcp/");
    expect(config.strictContract).toBe(true);
    expect(config.enableLegacySearch).toBe(false);
    expect(config.enableWriteTools).toBe(false);
    expect(config.enableFullGraph).toBe(false);
  });

  it("accepts explicit timeouts and feature switches", () => {
    const config = loadTkbAdapterConfig({
      TKB_MCP_URL: "http://webapp:8000/mcp/",
      TKB_CONNECT_TIMEOUT_MS: "2500",
      TKB_DEEP_TOOL_TIMEOUT_MS: "9000",
      TKB_ENABLE_WRITE_TOOLS: "yes",
    });
    expect(config.mcpUrl).toBe("http://webapp:8000/mcp/");
    expect(config.connectTimeoutMs).toBe(2500);
    expect(config.deepToolTimeoutMs).toBe(9000);
    expect(config.enableWriteTools).toBe(true);
  });
});
