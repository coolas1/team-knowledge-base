from __future__ import annotations

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.reflect import ReflectEngine
from src.engine.hindsight_components.types import RecallResult

from .fakes import FakeProviders, FakeRepository, candidate


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
