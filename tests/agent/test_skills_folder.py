import pytest

from src.agent.interface import SkillContext
from tests.conftest import FakeKnowledgeBase


@pytest.mark.asyncio
async def test_search_and_answer_uses_kb_directly():
    from src.agent.tkb.skills.search_and_answer.skill import run

    kb = FakeKnowledgeBase()
    ctx = SkillContext(kb=kb, llm=None, params={"query": "acme", "top_k": 5})
    res = await run(ctx)
    assert res.name == "search_and_answer"
    assert kb.recall_calls == ["acme"]
    assert "answer" in res.output and "sources" in res.output
