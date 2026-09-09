from __future__ import annotations

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.reflect import ReflectEngine
from src.engine.hindsight_components.types import (
    RecallResult,
    ReflectionContext,
    MentalModel,
)

from src.engine.hindsight_components.tests.fakes import (
    FakeProviders,
    FakeRepository,
    candidate,
)


class RecallSpy:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str, int | None]] = []

    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult:
        self.queries.append((query, mode, top_k))
        item = (
            candidate("memory-a", "first-hop evidence")
            if len(self.queries) == 1
            else candidate("memory-b", "second-hop evidence")
        )
        return RecallResult(
            results=[item],
            chunks={},
            entities={},
            trace={"query": query, "mode": mode},
        )


async def test_reflect_calls_recall_then_expands_missing_hop() -> None:
    recall = RecallSpy()
    engine = ReflectEngine(
        recall,
        FakeRepository(),
        FakeProviders(),
        HindsightOptions(),
    )

    result = await engine.reflect("multi-hop question")

    assert recall.queries == [
        ("multi-hop question", "deep", None),
        ("second hop", "deep", None),
    ]
    assert result.text.startswith("Grounded answer")
    assert len(result.tool_trace) == 2
    assert {item["id"] for item in result.based_on["world"]} == {
        "memory-a",
        "memory-b",
    }


async def test_reflect_forwards_recall_mode_and_top_k() -> None:
    recall = RecallSpy()
    engine = ReflectEngine(
        recall,
        FakeRepository(),
        FakeProviders(),
        HindsightOptions(),
    )

    await engine.reflect("question", mode="fast", top_k=4)

    assert recall.queries == [
        ("question", "fast", 4),
        ("second hop", "fast", 4),
    ]


class EmptyRecall:
    async def recall(
        self, query: str, *, mode: str = "deep", top_k: int | None = None
    ) -> RecallResult:
        return RecallResult(results=[], chunks={}, entities={}, trace={})


async def test_reflect_returns_not_found_when_no_evidence() -> None:
    engine = ReflectEngine(
        EmptyRecall(),
        FakeRepository(),
        FakeProviders(),
        HindsightOptions(),
    )

    result = await engine.reflect("unknown topic")

    assert result.text == "知识库中未找到与该问题相关的内容。"
    assert result.based_on == {}
    assert len(result.tool_trace) == 2


class AdaptiveProviders:
    def __init__(self, calls):
        self.calls = list(calls)

    async def json(self, _system, _user, *, timeout=600):
        return self.calls.pop(0)

    async def embed(self, texts, *, timeout=None):
        return [[1.0, 0.0] for _ in texts]


class AdaptiveRecall:
    def __init__(self):
        self.queries = []

    async def recall(self, query, **_kwargs):
        self.queries.append(query)
        item = candidate(f"memory-{len(self.queries)}", f"evidence {query}")
        return RecallResult(results=[item], chunks={}, entities={}, trace={})


class AdaptiveRepository(FakeRepository):
    async def expand_memory_record(self, memory_id):
        if memory_id.startswith("memory-"):
            return {"memory": {"id": memory_id}, "source_facts": []}
        return None


async def test_adaptive_reflect_replans_and_validates_actual_citations():
    recall = AdaptiveRecall()
    providers = AdaptiveProviders(
        [
            {"tool": "recall", "query": "first hop"},
            {"tool": "recall", "query": "new gap from first result"},
            {
                "tool": "done",
                "answer": "Grounded",
                "citations": [{"type": "memory", "id": "memory-2"}],
            },
        ]
    )
    engine = ReflectEngine(
        recall,
        AdaptiveRepository(),
        providers,
        HindsightOptions(adaptive_reflect_enabled=True),
    )

    result = await engine.reflect("question")

    assert recall.queries == ["first hop", "new gap from first result"]
    assert result.actual_citations == [{"type": "memory", "id": "memory-2"}]
    assert [step["tool"] for step in result.tool_trace[:3]] == [
        "recall",
        "recall",
        "done",
    ]


async def test_adaptive_reflect_repairs_invalid_citation_once():
    recall = AdaptiveRecall()
    providers = AdaptiveProviders(
        [
            {"tool": "recall", "query": "facts"},
            {
                "tool": "done",
                "answer": "Forged",
                "citations": [{"type": "memory", "id": "unknown"}],
            },
            {
                "tool": "done",
                "answer": "Repaired",
                "citations": [{"type": "memory", "id": "memory-1"}],
            },
        ]
    )
    result = await ReflectEngine(
        recall,
        AdaptiveRepository(),
        providers,
        HindsightOptions(adaptive_reflect_enabled=True),
    ).reflect("question")

    assert result.text == "Repaired"
    assert result.tool_trace[1]["output"]["status"] == "repair"
    assert len([step for step in result.tool_trace if step["tool"] == "done"]) == 2


async def test_stale_model_cannot_be_actual_citation():
    class ModelRepository(AdaptiveRepository):
        async def reflection_context(self, _query, _embedding):
            return ReflectionContext(
                mental_models=[
                    MentalModel(
                        id="model",
                        name="Old",
                        description="old",
                        summary="old state",
                        embedding=[1.0, 0.0],
                        freshness="stale",
                    )
                ]
            )

    providers = AdaptiveProviders(
        [
            {"tool": "search_mental_models", "query": "state"},
            {
                "tool": "done",
                "answer": "Old",
                "citations": [{"type": "mental_model", "id": "model"}],
            },
            {"tool": "done", "answer": "insufficient", "citations": []},
        ]
    )
    result = await ReflectEngine(
        AdaptiveRecall(),
        ModelRepository(),
        providers,
        HindsightOptions(adaptive_reflect_enabled=True),
    ).reflect("state")

    assert result.text == "insufficient"
    assert result.actual_citations == []


async def test_stale_observation_expands_to_current_fact():
    class StaleRecall:
        async def recall(self, _query, **_kwargs):
            item = candidate("observation", "old synthesis")
            item.memory_type = "observation"
            item.freshness = "stale"
            return RecallResult(results=[item], chunks={}, entities={}, trace={})

    class ExpansionRepository(AdaptiveRepository):
        async def expand_memory_record(self, memory_id):
            if memory_id == "observation":
                return {
                    "memory": {"id": memory_id},
                    "source_facts": [
                        {
                            "id": "current-fact",
                            "text": "Owner is Li",
                            "type": "world",
                            "document_id": "document",
                        }
                    ],
                }
            if memory_id == "current-fact":
                return {"memory": {"id": memory_id}, "source_facts": []}
            return None

    result = await ReflectEngine(
        StaleRecall(),
        ExpansionRepository(),
        AdaptiveProviders(
            [
                {"tool": "search_observations", "query": "owner"},
                {"tool": "expand", "memory_id": "observation"},
                {
                    "tool": "done",
                    "answer": "Owner is Li",
                    "citations": [{"type": "memory", "id": "current-fact"}],
                },
            ]
        ),
        HindsightOptions(adaptive_reflect_enabled=True),
    ).reflect("current owner")

    assert result.text == "Owner is Li"
    assert result.actual_citations[0]["id"] == "current-fact"


async def test_adaptive_reflect_stops_at_iteration_budget():
    result = await ReflectEngine(
        AdaptiveRecall(),
        AdaptiveRepository(),
        AdaptiveProviders(
            [
                {"tool": "recall", "query": "again"},
                {"tool": "recall", "query": "again"},
            ]
        ),
        HindsightOptions(adaptive_reflect_enabled=True, reflect_max_iterations=2),
    ).reflect("unbounded request")

    assert result.actual_citations == []
    assert result.text.startswith("现有证据不足")
    assert len([step for step in result.tool_trace if step["tool"] == "recall"]) == 2
