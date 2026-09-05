import { describe, expect, it } from "vitest";
import {
  loadPiAgentConfig,
  loadTkbAdapterConfig,
  validateDeadlineHierarchy,
} from "../src/config.js";
import { PiAgentRuntime } from "../src/runtime.js";

describe("loadTkbAdapterConfig", () => {
  it("uses safe defaults", () => {
    const config = loadTkbAdapterConfig({});
    expect(config.mcpUrl).toBe("http://localhost:8000/mcp/");
    expect(config.strictContract).toBe(true);
    expect(config.enableLegacySearch).toBe(false);
    expect(config.enableWriteTools).toBe(false);
    expect(config.enableFullGraph).toBe(false);
    expect(config.conversationMemoryEnabled).toBe(false);
    expect(config.conversationMemoryRecallTimeoutMs).toBe(5000);
    expect(config.conversationMemoryRecallLimit).toBe(5);
    expect(config.conversationMemoryContextBudgetChars).toBe(6000);
    expect(config.conversationMemoryRetentionContext).toBe(
      "Completed team conversation turn",
    );
    expect(config.deepToolTimeoutMs).toBe(60_000);
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

  it("validates conversation memory limits and context", () => {
    const config = loadTkbAdapterConfig({
      TKB_CONVERSATION_MEMORY_ENABLED: "true",
      TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS: "1200",
      TKB_CONVERSATION_MEMORY_RECALL_LIMIT: "10",
      TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS: "4000",
      TKB_CONVERSATION_MEMORY_RETENTION_CONTEXT: "Support conversation",
    });
    expect(config.conversationMemoryEnabled).toBe(true);
    expect(config.conversationMemoryRecallTimeoutMs).toBe(1200);
    expect(config.conversationMemoryRecallLimit).toBe(10);
    expect(config.conversationMemoryContextBudgetChars).toBe(4000);
    expect(config.conversationMemoryRetentionContext).toBe("Support conversation");

    for (const [key, value] of [
      ["TKB_CONVERSATION_MEMORY_RECALL_TIMEOUT_MS", "0"],
      ["TKB_CONVERSATION_MEMORY_RECALL_LIMIT", "21"],
      ["TKB_CONVERSATION_MEMORY_CONTEXT_BUDGET_CHARS", "-1"],
      ["TKB_CONVERSATION_MEMORY_RECALL_LIMIT", "1.5"],
    ] as const) {
      expect(() => loadTkbAdapterConfig({ [key]: value })).toThrow(key);
    }
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
    expect(config.maxRunSeconds).toBe(180);
    expect(config.turnReserveSeconds).toBe(60);
    expect(config.sessionDir).toBe("C:/tkb/.pi-agent-data/sessions");
    expect(config.transcriptDir).toBe("C:/tkb/.pi-agent-data/transcripts");
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

  it("accepts a transcript directory override and rejects the SDK session directory", () => {
    expect(loadPiAgentConfig({ PI_AGENT_TRANSCRIPT_DIR: "C:/durable/transcripts" }).transcriptDir)
      .toBe("C:/durable/transcripts");
    expect(() => loadPiAgentConfig({
      PI_AGENT_SESSION_DIR: "C:/same",
      PI_AGENT_TRANSCRIPT_DIR: "C:/same",
    })).toThrow("PI_AGENT_TRANSCRIPT_DIR");
  });

  it("rejects a deep-tool timeout that consumes the answer reserve", () => {
    const agent = loadPiAgentConfig({
      PI_AGENT_MAX_RUN_SECONDS: "120",
      PI_AGENT_TURN_RESERVE_SECONDS: "60",
    });
    const adapter = loadTkbAdapterConfig({ TKB_DEEP_TOOL_TIMEOUT_MS: "60000" });

    expect(() => validateDeadlineHierarchy(agent, adapter)).toThrow(
      /TKB_DEEP_TOOL_TIMEOUT_MS.*PI_AGENT_TURN_RESERVE_SECONDS.*PI_AGENT_MAX_RUN_SECONDS/,
    );
    expect(() => new PiAgentRuntime(agent, adapter)).toThrow(/Invalid timeout hierarchy/);
  });
});
