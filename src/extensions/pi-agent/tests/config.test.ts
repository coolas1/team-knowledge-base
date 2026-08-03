import { describe, expect, it } from "vitest";
import { loadPiAgentConfig, loadTkbAdapterConfig } from "../src/config.js";

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

describe("loadPiAgentConfig", () => {
  it("supports a local Ollama runtime without sharing Engine settings", () => {
    const config = loadPiAgentConfig({
      PI_AGENT_CWD: "C:/tkb",
      PI_AGENT_MODEL: "qwen3:14b",
    });
    expect(config.provider).toBe("ollama");
    expect(config.modelApiKey).toBe("ollama");
    expect(config.maxToolCalls).toBe(12);
    expect(config.maxRunSeconds).toBe(300);
    expect(config.sessionDir).toBe("C:/tkb/.pi-agent-data/sessions");
    expect(config.exposeThinking).toBe(false);
    expect(config.exposeToolResults).toBe(false);
  });

  it("only exposes sensitive streaming details when explicitly enabled", () => {
    const config = loadPiAgentConfig({
      PI_AGENT_EXPOSE_THINKING: "true",
      PI_AGENT_EXPOSE_TOOL_RESULTS: "yes",
    });

    expect(config.exposeThinking).toBe(true);
    expect(config.exposeToolResults).toBe(true);
  });

  it("accepts external OpenAI-compatible model settings", () => {
    const config = loadPiAgentConfig({
      PI_AGENT_CWD: "C:/tkb",
      PI_AGENT_PROVIDER: "deepseek",
      PI_AGENT_MODEL: "deepseek-chat",
      PI_AGENT_BASE_URL: "https://api.example/v1",
      PI_AGENT_API_KEY: "test-key",
      PI_AGENT_MAX_TOOL_CALLS: "8",
    });
    expect(config.provider).toBe("deepseek");
    expect(config.modelBaseUrl).toBe("https://api.example/v1");
    expect(config.modelApiKey).toBe("test-key");
    expect(config.maxToolCalls).toBe(8);
  });

  it("inherits the configured shared LLM when Pi has no model override", () => {
    const config = loadPiAgentConfig({
      LLM_PROVIDER: "custom",
      LLM_MODEL: "configured-model",
      LLM_BASE_URL: "https://llm.example/v1",
      LLM_API_KEY: "shared-secret",
    });

    expect(config.provider).toBe("custom");
    expect(config.model).toBe("configured-model");
    expect(config.modelName).toBe("configured-model");
    expect(config.modelBaseUrl).toBe("https://llm.example/v1");
    expect(config.modelApiKey).toBe("shared-secret");
  });

  it("does not inherit a disabled shared LLM", () => {
    const config = loadPiAgentConfig({
      LLM_PROVIDER: "todo",
      LLM_MODEL: "placeholder",
      LLM_BASE_URL: "https://example.invalid/v1",
    });

    expect(config.provider).toBe("ollama");
    expect(config.model).toBe("qwen3:14b");
    expect(config.modelBaseUrl).toBe("http://localhost:11434/v1");
  });
});
