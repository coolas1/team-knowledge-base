from __future__ import annotations

from datetime import datetime
from typing import Any

from src.engine.hindsight_components.types import (
    MemoryProfile,
    RecallCandidate,
    ReflectionContext,
    RetainPlan,
)


def candidate(
    memory_id: str,
    text: str,
    *,
    semantic: float = 0.0,
    keyword: float = 0.0,
    graph: float = 0.0,
    temporal: float = 0.0,
) -> RecallCandidate:
    return RecallCandidate(
        id=memory_id,
        document_id=f"document-{memory_id}",
        title=f"{memory_id}.md",
        text=text,
        source_text=f"source: {text}",
        chunk_index=0,
        embedding=[1.0, 0.0],
        semantic_score=semantic,
        keyword_score=keyword,
        graph_score=graph,
        temporal_score=temporal,
    )


class FakeRepository:
    def __init__(self) -> None:
        self.plan: RetainPlan | None = None
        self.calls = {
            "semantic": 0,
            "keyword": 0,
            "graph": 0,
            "temporal": 0,
        }
        self.a = candidate("memory-a", "Alice works on coral", semantic=0.9, graph=0.8)
        self.b = candidate(
            "memory-b", "Survey happened in 2024", keyword=0.8, temporal=0.7
        )

    async def replace_document(self, plan: RetainPlan) -> None:
        self.plan = plan

    async def semantic_neighbors(
        self,
        embedding: list[float],
        *,
        exclude_document_id: str,
        limit: int,
    ) -> list[tuple[str, float]]:
        return [("existing-memory", 0.9)]

    async def semantic_search(
        self, embedding: list[float], limit: int
    ) -> list[RecallCandidate]:
        self.calls["semantic"] += 1
        return [self.a, self.b]

    async def keyword_search(self, query: str, limit: int) -> list[RecallCandidate]:
        self.calls["keyword"] += 1
        return [self.b, self.a]

    async def graph_search(
        self, entities: list[str], limit: int
    ) -> list[RecallCandidate]:
        self.calls["graph"] += 1
        return [self.a]

    async def temporal_search(
        self,
        start: datetime | None,
        end: datetime | None,
        limit: int,
    ) -> list[RecallCandidate]:
        self.calls["temporal"] += 1
        return [self.b]

    async def entity_states(self, memory_ids: list[str]) -> dict[str, Any]:
        return {"Alice": {"canonical_name": "Alice", "observations": []}}

    async def reflection_context(
        self, query: str, query_embedding: list[float]
    ) -> ReflectionContext:
        return ReflectionContext(profile=MemoryProfile(background="TKB maintainer"))


class FakeProviders:
    def __init__(self) -> None:
        self.json_calls: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    async def json(
        self, system: str, user: str, *, timeout: float = 600
    ) -> dict[str, Any]:
        self.json_calls.append(system)
        if system.startswith("You extract exhaustive"):
            return {
                "facts": [
                    {
                        "text": "Alice started the survey",
                        "type": "experience",
                        "entities": ["Alice"],
                        "occurred_start": "2024-01-01T00:00:00Z",
                        "confidence": 0.9,
                    },
                    {
                        "text": "The survey produced a report",
                        "type": "world",
                        "entities": ["Alice", "Survey"],
                        "occurred_start": "2024-01-02T00:00:00Z",
                        "caused_by": [0],
                    },
                ]
            }
        if system.startswith("Consolidate only"):
            return {
                "observations": [
                    {
                        "text": "Alice's survey produced a report",
                        "source_indexes": [0, 1],
                        "entities": ["Alice", "Survey"],
                        "confidence": 0.95,
                    }
                ]
            }
        if system.startswith("Analyze a memory retrieval query"):
            return {
                "entities": ["Alice"],
                "start": "2024-01-01T00:00:00Z",
                "end": None,
                "subqueries": [],
            }
        if system.startswith("Rank memories"):
            ids = [line.split(":", 1)[0] for line in user.splitlines() if ": " in line]
            return {
                "ranking": [
                    {"id": memory_id, "score": 1 - index / 10}
                    for index, memory_id in enumerate(ids)
                ]
            }
        if system.startswith("Plan evidence retrieval"):
            return {"subqueries": ["second hop"]}
        return {}

    async def text(self, system: str, user: str, *, timeout: float = 600) -> str:
        return "Grounded answer [memory-a] [memory-b]."
