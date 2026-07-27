"""Skill (a): recall knowledge-base context for a query and synthesize an answer.

Harness-agnostic: when ctx.llm is provided it synthesizes the answer; when not,
it returns the formatted recall context and lets the harness (e.g. codex) answer.
"""
from __future__ import annotations

from src.agent.interface import SkillContext, SkillResult


def _format_context(recall: dict) -> str:
    lines = []
    for i, c in enumerate(recall.get("chunks", []), 1):
        lines.append(f"[{i}] ({c.get('title', '')}) {c.get('chunk_text', '')}")
    ents = recall.get("related_entities", [])
    if ents:
        lines.append("相关实体: " + ", ".join(e.get("name", "") for e in ents))
    return "\n".join(lines)


def _answer_prompt(query: str, context: str) -> str:
    return (
        f"你是知识库助手。根据以下检索到的资料回答问题。若资料不足请说明。\n\n"
        f"问题: {query}\n\n资料:\n{context}\n\n回答:"
    )


class SearchAndAnswerSkill:
    name = "search_and_answer"
    description = "Recall knowledge-base context for a query and synthesize an answer."

    async def run(self, ctx: SkillContext) -> SkillResult:
        query = ctx.params.get("query", "")
        top_k = int(ctx.params.get("top_k", 10))
        recall = await ctx.engine.recall(query, top_k=top_k)
        context = _format_context(recall)
        if ctx.llm is not None:
            answer = await ctx.llm.complete(_answer_prompt(query, context))
        else:
            answer = context
        return SkillResult(
            name=self.name,
            output={"query": query, "answer": answer, "sources": recall},
        )
