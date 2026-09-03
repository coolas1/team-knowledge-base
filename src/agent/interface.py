"""Plugin contract: data + a loader (replaces the old AgentPlugin/Skill/
EngineClient Protocols). Skills/Hooks are data/code the loader produces."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Protocol

from src.engine.interface import KnowledgeBase, KnowledgeQuery

if TYPE_CHECKING:
    from src.agent.policy import HookPolicy


class LlmClient(Protocol):
    """Minimal LLM interface a skill uses for synthesis."""
    async def complete(self, prompt: str) -> str: ...


@dataclass
class SkillContext:
    kb: KnowledgeBase
    llm: LlmClient | None = None
    query: KnowledgeQuery | None = None
    params: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    name: str
    output: dict


@dataclass
class LoadedSkill:
    name: str
    description: str
    inputs: dict
    run: Callable[[SkillContext], Awaitable[SkillResult]]


@dataclass
class McpSpec:
    endpoint: str | None = None


@dataclass
class PluginManifest:
    name: str
    version: str = "0.1.0"
    mcp: McpSpec = field(default_factory=McpSpec)
    skills: list[str] = field(default_factory=list)


@dataclass
class LoadedPlugin:
    manifest: PluginManifest
    skills: dict[str, LoadedSkill]
    hooks: "HookPolicy"
    mcp: McpSpec | None = None
