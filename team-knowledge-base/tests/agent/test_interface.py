from src.agent.interface import (
    AgentPlugin, EngineClient, LlmClient, Skill, SkillContext, SkillResult,
)
from src.agent.memory import MemoryStore


def test_skill_context_defaults():
    ctx = SkillContext(engine=None)  # type: ignore[arg-type]
    assert ctx.llm is None
    assert ctx.params == {}


def test_skill_result_holds_dict():
    r = SkillResult(name="x", output={"a": 1})
    assert r.output["a"] == 1


def test_protocols_are_importable():
    # Protocols exist and are usable as types
    for p in (Skill, EngineClient, AgentPlugin, LlmClient, MemoryStore):
        assert p is not None
