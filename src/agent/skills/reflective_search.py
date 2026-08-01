"""Query Hindsight through the shared EngineClient contract."""

from __future__ import annotations

from src.agent.interface import SkillContext, SkillResult


def _format_sources(sources: list[dict]) -> str:
    return "\n".join(
        f"[{index}] ({source.get('title', '')}) {source.get('chunk_text', '')}"
        for index, source in enumerate(sources, 1)
    )


def _answer_prompt(query: str, context: str) -> str:
    return (
        "你是知识库助手。根据以下 Hindsight 检索资料回答问题。"
        "若资料不足请明确说明。\n\n"
        f"问题: {query}\n\n资料:\n{context}\n\n回答:"
    )


class ReflectiveSearchSkill:
    name = "reflective_search"
    description = "Use Hindsight recall or reflect to query the knowledge base."

    async def run(self, ctx: SkillContext) -> SkillResult:
        query = ctx.params.get("query", "")
        strategy = ctx.params.get("strategy", "auto")
        mode = ctx.params.get("mode", "deep")
        top_k = int(ctx.params.get("top_k", 10))
        needs_answer = bool(ctx.params.get("needs_answer", True))

        result = await ctx.engine.query(
            query,
            strategy=strategy,
            mode=mode,
            top_k=top_k,
            needs_answer=needs_answer,
        )
        sources = result.get("sources", [])
        answer = result.get("answer")
        if not answer:
            context = _format_sources(sources)
            if ctx.llm is not None and needs_answer:
                answer = await ctx.llm.complete(_answer_prompt(query, context))
            else:
                answer = context

        return SkillResult(
            name=self.name,
            output={**result, "query": query, "answer": answer},
        )
