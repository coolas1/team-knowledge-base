import type { KnowledgeConfig } from "../config.ts";

/** 火山方舟 chat/completions 通用客户端（VLM 图片描述、实体抽取、维护 agentic 循环共用） */

export type ArkContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

export interface ArkToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export type ArkMessage =
  | { role: "system" | "user"; content: string | ArkContentPart[] }
  | { role: "assistant"; content: string; tool_calls?: ArkToolCall[] }
  | { role: "tool"; content: string; tool_call_id: string };

export interface ArkTool {
  type: "function";
  function: { name: string; description: string; parameters: Record<string, unknown> };
}

interface ArkChatResponse {
  choices: Array<{ message: { content: string | null; tool_calls?: ArkToolCall[] } }>;
}

/** 带工具的完整调用，返回 assistant 消息（content + tool_calls） */
export async function arkChatMessage(
  config: KnowledgeConfig,
  model: string,
  messages: ArkMessage[],
  options: { maxTokens?: number; tools?: ArkTool[] } = {},
): Promise<{ content: string; toolCalls: ArkToolCall[] }> {
  if (!config.ark.apiKey) {
    throw new Error("缺少 ARK API Key：设置环境变量 ARK_API_KEY 或在 ~/.pi/agent/knowledge.json 配置 ark.apiKey");
  }
  const response = await fetch(`${config.ark.baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.ark.apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      max_tokens: options.maxTokens ?? 2048,
      ...(options.tools ? { tools: options.tools } : {}),
    }),
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`ARK chat 请求失败 (${response.status}): ${body.slice(0, 500)}`);
  }
  const payload = (await response.json()) as ArkChatResponse;
  const message = payload.choices[0]?.message;
  return { content: message?.content ?? "", toolCalls: message?.tool_calls ?? [] };
}

export async function arkChat(
  config: KnowledgeConfig,
  model: string,
  messages: ArkMessage[],
  maxTokens = 2048,
): Promise<string> {
  const { content } = await arkChatMessage(config, model, messages, { maxTokens });
  return content;
}
