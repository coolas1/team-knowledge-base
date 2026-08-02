import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  createAgentSession,
  DefaultResourceLoader,
  getAgentDir,
  SessionManager,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { searchToolDefinitions } from "./tools.ts";

/**
 * B 层 knowledge_search 子代理：
 * 进程内 SDK 子循环，隔离上下文窗口——多轮检索/图谱漫游/读全文都在子会话中消耗，
 * 主会话只收到最终综合答案。适合需要多跳探索的复杂问题；简单查询用 A 层工具即可。
 */

const SUBAGENT_PARAMS = Type.Object({
  question: Type.String({
    description: "要在知识库中研究的问题（自然语言，含必要上下文）",
  }),
});

const SUBAGENT_PROMPT = `你是个人知识库检索专家。用户会给你一个问题，你的任务是在知识库中彻底研究并给出综合回答。

知识库包含三类内容：doc（文档笔记）、memory（提炼记忆）、session（历史会话档案）。

工作方法：
1. 先用 knowledge_hybrid_search 检索（可换不同措辞多查几次，必要时用 kinds 过滤）
2. 对关键命中用 knowledge_read_doc 读全文，避免只凭片段下结论
3. 用 knowledge_graph_expand 沿实体/文档关联发现检索没直接命中的相关材料
4. 信息足够后停止探索，输出最终回答

回答要求：
- 直接回答问题，其后列出依据的来源路径（source_path）
- 知识库中没有答案时明确说"知识库中未找到"，不要编造
- 简洁，不复述检索过程`;

const MAX_TURNS = 15;

export function registerKnowledgeSubagent(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "knowledge_search",
    label: "Knowledge Agent",
    description:
      "委托子代理在个人知识库中深入研究一个问题（多轮检索+图谱漫游+读全文，隔离上下文），返回综合答案与来源。适合复杂/多跳问题；简单查询直接用 knowledge_hybrid_search。",
    promptSnippet: "knowledge_search: 委托子代理深入研究知识库问题（复杂/多跳查询）",
    parameters: SUBAGENT_PARAMS,
    async execute(_toolCallId, params, signal, onUpdate, ctx) {
      const loader = new DefaultResourceLoader({
        cwd: ctx.cwd,
        agentDir: getAgentDir(),
        noExtensions: true,
        noSkills: true,
        noPromptTemplates: true,
        noThemes: true,
        noContextFiles: true,
        systemPromptOverride: () => SUBAGENT_PROMPT,
        appendSystemPromptOverride: () => [],
      });
      await loader.reload();

      const { session } = await createAgentSession({
        cwd: ctx.cwd,
        model: ctx.model,
        resourceLoader: loader,
        sessionManager: SessionManager.inMemory(ctx.cwd),
        noTools: "builtin", // "all" 会生成空 allowlist 把 customTools 一并禁掉
        customTools: searchToolDefinitions(),
      });

      try {
        let turns = 0;
        session.subscribe((event) => {
          if (event.type === "tool_execution_start") {
            turns++;
            onUpdate?.({
              content: [{ type: "text", text: `子代理检索中（第 ${turns} 次工具调用）...` }],
              details: undefined,
            });
          }
        });
        if (signal) {
          signal.addEventListener("abort", () => session.abort(), { once: true });
        }

        await session.prompt(
          `${params.question}\n\n（最多 ${MAX_TURNS} 次工具调用，超出前请直接给出当前结论）`,
        );

        // 取最后一条 assistant 文本作为答案
        let answer = "";
        for (const message of session.state.messages) {
          if (message.role === "assistant" && Array.isArray(message.content)) {
            const text = message.content
              .filter((part): part is { type: "text"; text: string } => part.type === "text")
              .map((part) => part.text)
              .join("\n")
              .trim();
            if (text !== "") answer = text;
          }
        }
        return {
          content: [{ type: "text" as const, text: answer !== "" ? answer : "子代理未产出回答。" }],
          details: undefined as unknown,
          isError: answer === "",
        };
      } finally {
        session.dispose();
      }
    },
  });
}
