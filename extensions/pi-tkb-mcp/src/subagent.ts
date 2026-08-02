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
 * Agentic RAG 子代理：tkb_research
 *
 * 进程内 SDK 子循环，隔离上下文窗口——多轮检索/图谱漫游/读文档都在子会话中消耗，
 * 主会话只收到最终综合答案。实现真正的 agentic RAG：
 *   检索 → 阅读 → 推理 → 再检索 → ... → 综合回答
 */

const SUBAGENT_PARAMS = Type.Object({
  question: Type.String({
    description: "要在团队知识库中研究的问题（自然语言，含必要上下文）",
  }),
});

const SUBAGENT_PROMPT = `你是团队知识库检索专家。用户会给你一个问题，你的任务是在团队知识库中彻底研究并给出综合回答。

知识库是一个 GraphRAG 系统，包含：
- 多格式文档（PDF、DOCX、PPTX、Markdown、图片 OCR）
- 三层知识图谱（实体、关系、文本块）
- 语义向量索引 + Reranker

可用工具：
- tkb_search: 语义检索（向量 + 图谱增强），返回相关片段与实体
- tkb_get_document: 按 ID 读取文档全文
- tkb_query_graph: 查询知识图谱中的实体关系与邻居
- tkb_list_documents: 浏览文档列表
- tkb_get_full_graph: 获取完整知识图谱

工作方法：
1. 先用 tkb_search 检索（可换不同措辞多查几次）
2. 对关键命中用 tkb_get_document 读全文，避免只凭片段下结论
3. 用 tkb_query_graph 沿实体关系发现检索没直接命中的相关材料
4. 信息足够后停止探索，输出最终回答

回答要求：
- 直接回答问题，其后列出依据的来源文档（doc_id + title）
- 知识库中没有答案时明确说"知识库中未找到"，不要编造
- 简洁，不复述检索过程`;

const MAX_TURNS = 15;

export function registerTkbSubagent(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "tkb_research",
    label: "TKB Research Agent",
    description:
      "委托子代理在团队知识库中深入研究一个问题（多轮 GraphRAG 检索 + 图谱漫游 + 读全文，隔离上下文），返回综合答案与来源。适合复杂/多跳问题；简单查询直接用 tkb_search。",
    promptSnippet: "tkb_research: 委托子代理深入研究团队知识库问题（复杂/多跳查询）",
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
        noTools: "builtin",
        customTools: searchToolDefinitions(),
      });

      try {
        let turns = 0;
        session.subscribe((event) => {
          if (event.type === "tool_execution_start") {
            turns++;
            onUpdate?.({
              content: [{ type: "text", text: `TKB 子代理检索中（第 ${turns} 次工具调用）...` }],
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
          content: [{ type: "text" as const, text: answer !== "" ? answer : "TKB 子代理未产出回答。" }],
          details: undefined as unknown,
          isError: answer === "",
        };
      } finally {
        session.dispose();
      }
    },
  });
}
