"""BFF agent-skill routes: invoke harness-agnostic skills in-process."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.agent.interface import EngineClient, LlmClient, SkillContext
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/agent", tags=["agent"])


class AskRequest(BaseModel):
    query: str
    top_k: int = 10


def _find_skill(name: str):
    plugin = deps.get_plugin()
    for s in plugin.skills():
        if s.name == name:
            return s
    raise HTTPException(404, f"skill not found: {name}")


@router.post("/ask")
async def ask(
    body: AskRequest,
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    skill = _find_skill("search_and_answer")
    ctx = SkillContext(engine=engine, llm=llm, params={"query": body.query, "top_k": body.top_k})
    result = await skill.run(ctx)
    return result.output


@router.post("/ingest-summarize")
async def ingest_summarize(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    skill = _find_skill("ingest_and_summarize")
    ctx = SkillContext(engine=engine, llm=llm, params={"name": file.filename, "data": data})
    result = await skill.run(ctx)
    return result.output
