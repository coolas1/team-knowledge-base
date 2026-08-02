"""Skill (b): ingest a file into the knowledge base and produce a summary.

Harness-agnostic: when ctx.llm is provided it summarizes the ingested text;
otherwise it returns the engine-generated document overview.
"""
from __future__ import annotations

from src.agent.interface import SkillContext, SkillResult


def _summary_prompt(name: str, text: str) -> str:
    return (
        f"请为以下文档生成 2-3 句话的摘要。\n\n文档: {name}\n\n内容:\n{text[:4000]}\n\n摘要:"
    )


class IngestAndSummarizeSkill:
    name = "ingest_and_summarize"
    description = "Ingest a file into the knowledge base and produce a summary."

    async def run(self, ctx: SkillContext) -> SkillResult:
        name = ctx.params["name"]
        data: bytes = ctx.params["data"]
        doc = await ctx.engine.ingest(name, data)
        detail = await ctx.engine.get_document(doc.get("id", ""))
        text = (detail or {}).get("raw_text", "")
        if ctx.llm is not None and text:
            summary = await ctx.llm.complete(_summary_prompt(name, text))
        else:
            summary = (detail or {}).get("overview", "")
        return SkillResult(name=self.name, output={"doc": doc, "summary": summary})
