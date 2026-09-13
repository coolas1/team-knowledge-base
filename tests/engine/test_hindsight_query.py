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

    async def recall(self, query, *, mode="deep", top_k=None, filters=None):
        return self.result

    async def reflect(self, query, *, mode="deep", top_k=None, filters=None):  # pragma: no cover
        raise AssertionError("reflect not exercised here")


async def _recall(result: RecallResult, **overrides) -> object:
    return await HindsightQueryService(FakeCore(result)).query(
        KnowledgeQueryRequest(
            query="compare",
            strategy="recall",
            mode="deep",
            needs_answer=False,
            **overrides,
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
    # based_on is opt-in and never repeats the evidence text
    assert out.based_on == {}

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


async def test_source_metadata_has_no_document_record():
    # The full document record stays behind tkb_get_document; metadata
    # carries identifying fields and scores only.
    candidate = _candidate(0, "body text")
    result = RecallResult(
        results=[candidate],
        chunks={},
        entities={},
        documents={"doc-0": {"title": "doc-0.md", "raw_text": "x" * 5_000}},
        trace={},
    )

    out = await _recall(result)

    assert "document" not in out.sources[0].metadata
    assert out.sources[0].metadata["source_type"] == "upload"
    assert out.sources[0].metadata["scores"]["final"] == 1.0


async def test_based_on_is_opt_in_and_compact():
    result = RecallResult(
        results=[_candidate(0, "evidence"), _candidate(1, "more evidence")],
        chunks={},
        entities={},
        trace={},
    )

    default = await _recall(result)
    assert default.based_on == {}

    opted_in = await _recall(
        result, include=("chunks", "entities", "based_on")
    )
    assert set(opted_in.based_on) == {"world"}
    grouped = opted_in.based_on["world"]
    assert [item["id"] for item in grouped] == ["m0", "m1"]
    # ids and scores, never the evidence text a second time
    assert all("text" not in item for item in grouped)
    assert grouped[0]["scores"]["final"] == 1.0
    serialized = str(opted_in.based_on)
    assert "evidence" not in serialized


async def test_related_entities_are_capped(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "engine_tools_entities_max", 2)
    entities = {f"entity-{i}": {"canonical_name": f"entity-{i}"} for i in range(5)}
    result = RecallResult(results=[], chunks={}, entities=entities, trace={})

    out = await _recall(result)

    assert [item["name"] for item in out.related_entities] == [
        "entity-0",
        "entity-1",
    ]


async def test_reflect_based_on_keeps_provenance_without_evidence_text():
    from src.engine.hindsight_components.types import ReflectResult

    class ReflectCore:
        async def recall(self, query, *, mode="deep", top_k=None):  # pragma: no cover
            raise AssertionError("reflect strategy must not recall")

        async def reflect(self, query, *, mode="deep", top_k=None):
            return ReflectResult(
                text="answer",
                based_on={
                    "world": [
                        {
                            "id": "m0",
                            "text": "evidence body",
                            "type": "world",
                            "document_id": "doc-0",
                            "scores": {"final": 0.9},
                        }
                    ],
                    "actual_citations": [{"id": "m0", "type": "memory"}],
                },
                tool_trace=[],
                actual_citations=[{"id": "m0", "type": "memory"}],
            )

    out = await HindsightQueryService(ReflectCore()).query(
        KnowledgeQueryRequest(query="compare", strategy="reflect", needs_answer=True)
    )

    assert out.answer == "answer"
    # evidence text lives once, in sources
    assert out.sources[0].chunk_text == "evidence body"
    assert out.based_on["world"] == [
        {
            "id": "m0",
            "type": "world",
            "document_id": "doc-0",
            "scores": {"final": 0.9},
        }
    ]
    # citation provenance passes through untouched
    assert out.based_on["actual_citations"] == [{"id": "m0", "type": "memory"}]
