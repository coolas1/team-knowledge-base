"""search_and_answer: recall context via KnowledgeBase, synthesize with the LLM
(when provided); else return the formatted context for the harness to answer."""
from __future__ import annotations

from src.engine.interface import NOT_FOUND_ANSWER, RecallRequest
from src.agent.interface import SkillContext, SkillResult


def _format_context(recall) -> str:
    lines = [
        f"[{i}] ({c.title}) {c.chunk_text}"
        for i, c in enumerate(recall.chunks, 1)
    ]
    if recall.related_entities:
        lines.append("相关实体: " + ", ".join(e.get("name", "") for e in recall.related_entities))
    return "\n".join(lines)


def _answer_prompt(query: str, context: str) -> str:
    return (
        f"你是知识库助手。根据以下检索到的资料回答问题。若资料不足请说明。\n\n"
        f"问题: {query}\n\n资料:\n{context}\n\n回答:"
    )


async def run(ctx: SkillContext) -> SkillResult:
    query = ctx.params.get("query", "")
    top_k = int(ctx.params.get("top_k", 10))
    recall = await ctx.kb.recall(RecallRequest(query=query, top_k=top_k))
    context = _format_context(recall)
    if not recall.chunks:
        answer = NOT_FOUND_ANSWER
    elif ctx.llm:
        answer = await ctx.llm.complete(_answer_prompt(query, context))
    else:
        answer = context
    return SkillResult(name="search_and_answer", output={"query": query, "answer": answer, "sources": recall})
