"""Agent module contracts.

Skills are harness-agnostic: callable in-process (by the webapp BFF) and
wrappable by any harness plugin (codex first). Skills call an EngineClient,
never engine internals. Memory is interface-only (impl deferred).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class LlmClient(Protocol):
    """Minimal LLM interface a skill uses for synthesis (answer/summary)."""
    async def complete(self, prompt: str) -> str: ...


@dataclass
class SkillContext:
    engine: "EngineClient"
    llm: LlmClient | None = None
    params: dict = field(default_factory=dict)


@dataclass
class SkillResult:
    name: str
    output: dict


class Skill(Protocol):
    name: str
    description: str
    async def run(self, ctx: SkillContext) -> SkillResult: ...


class EngineClient(Protocol):
    """Uniform client over in-process / MCP transports. Skills call this,
    never engine internals. All methods return JSON-ish dicts."""

    async def recall(self, query: str, top_k: int = 10) -> dict: ...
    async def ingest(self, name: str, data: bytes) -> dict: ...
    async def get_document(self, doc_id: str) -> dict: ...
    async def get_graph(self, entity: str | None = None) -> dict: ...
    async def get_neighbors(self, entity: str) -> dict: ...
    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict: ...
    async def remove(self, doc_id: str) -> dict: ...


class AgentPlugin(Protocol):
    """What each harness implementation exposes."""
    harness: str
    def skills(self) -> list[Skill]: ...
