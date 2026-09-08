"""ingest_and_summarize: ingest via KnowledgeBase, summarize with the LLM
(when provided); else return the engine-generated overview."""
from __future__ import annotations

from src.engine.interface import IngestSource
from src.agent.interface import SkillContext, SkillResult


def _summary_prompt(name: str, text: str) -> str:
    return f"请为以下文档生成 2-3 句话的摘要。\n\n文档: {name}\n\n内容:\n{text[:4000]}\n\n摘要:"


async def run(ctx: SkillContext) -> SkillResult:
    name = ctx.params["name"]
    data: bytes = ctx.params["data"]
    ref = await ctx.kb.ingest(IngestSource(name=name, data=data))
    detail = await ctx.kb.get_document(ref.id) or {}
    text = detail.get("raw_text", "")
    summary = await ctx.llm.complete(_summary_prompt(name, text)) if (ctx.llm and text) else ref.overview
    return SkillResult(name="ingest_and_summarize", output={"doc": ref, "summary": summary})
