"""HindsightQueryService recall shaping: evidence budget + trace markers."""

from src.engine.hindsight_components.query import HindsightQueryService
from src.engine.hindsight_components.types import RecallCandidate, RecallResult
from src.engine.interface import KnowledgeQueryRequest


def _candidate(n: int, text: str) -> RecallCandidate:
    return RecallCandidate(
        id=f"m{n}",
        document_id=f"doc-{n}",
        title=f"doc-{n}.md",
        text=text,
        source_text=text,
        chunk_index=n,
        memory_type="world",
        source_type="upload",
        final_score=1.0 / (n + 1),  # ranked best-first
    )


class FakeCore:
    def __init__(self, result: RecallResult) -> None:
        self.result = result

    async def recall(self, query, *, mode="deep", top_k=None):
        return self.result

    async def reflect(self, query, *, mode="deep", top_k=None):  # pragma: no cover
        raise AssertionError("reflect not exercised here")


async def _recall(result: RecallResult) -> object:
    return await HindsightQueryService(FakeCore(result)).query(
        KnowledgeQueryRequest(
            query="compare", strategy="recall", mode="deep", needs_answer=False
        )
    )


async def test_deep_evidence_is_budgeted_and_reported(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "engine_tools_deep_excerpt_chars", 100)
    monkeypatch.setattr(settings, "engine_tools_deep_total_chars", 250)

    items = [_candidate(i, "e" * 400) for i in range(6)]
    result = RecallResult(
        results=items,
        chunks={},
        entities={},
        trace={"outcome": "success", "degraded": False},
    )

    out = await _recall(result)

    # total payload within budget; per-excerpt capped
    total = sum(len(source.chunk_text) for source in out.sources)
    assert total <= 250
    assert all(len(source.chunk_text) <= 100 for source in out.sources)

    # lowest-ranked dropped first: the two top-ranked excerpts survive intact
    assert [source.memory_id for source in out.sources] == ["m0", "m1"]
    assert out.sources[0].chunk_text == "e" * 100
    assert [ev["text"] for group in out.based_on.values() for ev in group] == [
        "e" * 100,
        "e" * 100,
    ]

    # trace reports the trim beside degraded, without flipping the outcome
    assert out.trace["evidence_trimmed"] is True
    assert out.trace["evidence_kept"] == 2
    assert out.trace["evidence_dropped"] == 4
    assert out.trace["evidence_chars"] == 200
    assert out.trace["outcome"] == "success"
    assert out.trace["degraded"] is False


async def test_deep_evidence_excerpt_cap_trims_without_dropping(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "engine_tools_deep_excerpt_chars", 100)
    monkeypatch.setattr(settings, "engine_tools_deep_total_chars", 12000)

    result = RecallResult(
        results=[_candidate(0, "x" * 5000)],
        chunks={},
        entities={},
        trace={"outcome": "success", "degraded": False},
    )

    out = await _recall(result)

    assert len(out.sources) == 1
    assert len(out.sources[0].chunk_text) == 100
    assert out.trace["evidence_trimmed"] is True
    assert out.trace["evidence_dropped"] == 0


async def test_small_evidence_set_is_not_trimmed(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "engine_tools_deep_excerpt_chars", 2000)
    monkeypatch.setattr(settings, "engine_tools_deep_total_chars", 12000)

    result = RecallResult(
        results=[_candidate(0, "short"), _candidate(1, "also short")],
        chunks={},
        entities={},
        trace={"outcome": "success", "degraded": False},
    )

    out = await _recall(result)

    assert [source.chunk_text for source in out.sources] == ["short", "also short"]
    assert out.trace["evidence_trimmed"] is False
    assert out.trace["evidence_kept"] == 2
    assert out.trace["evidence_dropped"] == 0
