"""Reflective search skill: query Hindsight through the KnowledgeQuery contract."""

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


async def run(ctx: SkillContext) -> SkillResult:
    query = ctx.params.get("query", "")
    strategy = ctx.params.get("strategy", "auto")
    mode = ctx.params.get("mode", "deep")
    top_k = int(ctx.params.get("top_k", 10))
    needs_answer = bool(ctx.params.get("needs_answer", True))

    if ctx.query is None:
        raise RuntimeError("Hindsight query service not available in this context")

    from src.engine.interface import NOT_FOUND_ANSWER, KnowledgeQueryRequest

    result = await ctx.query.query(
        KnowledgeQueryRequest(
            query=query,
            strategy=strategy,
            mode=mode,
            top_k=top_k,
            needs_answer=needs_answer,
        )
    )

    sources = [
        {
            "title": s.title,
            "chunk_text": s.chunk_text,
            "doc_id": s.doc_id,
            "memory_id": s.memory_id,
        }
        for s in result.sources
    ]
    answer = result.answer
    if not answer:
        if not sources:
            answer = NOT_FOUND_ANSWER
        elif ctx.llm is not None and needs_answer:
            context = _format_sources(sources)
            answer = await ctx.llm.complete(_answer_prompt(query, context))
        else:
            answer = _format_sources(sources)

    return SkillResult(
        name="reflective_search",
        output={
            "query": query,
            "answer": answer,
            "sources": sources,
            "strategy_used": result.strategy_used,
            "related_entities": result.related_entities,
        },
    )
