from __future__ import annotations

import pytest

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.recall import RecallEngine
from src.engine.hindsight_components.types import RecallFilter

from .fakes import FakeProviders, FakeRepository, candidate


async def test_deep_recall_runs_four_arms_and_returns_trace() -> None:
    repository = FakeRepository()
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("What did Alice do in 2024?")

    assert repository.calls == {
        "semantic": 1,
        "keyword": 1,
        "graph": 1,
        "temporal": 1,
    }
    assert result.trace["arm_counts"] == {
        "semantic": 2,
        "keyword": 2,
        "graph": 1,
        "temporal": 1,
    }
    assert result.trace["algorithm"].endswith("RRF/neural-rerank/MMR")
    assert result.entities["Alice"]["canonical_name"] == "Alice"
    assert len(result.results) == 2


async def test_fast_recall_skips_llm_graph_temporal_and_rerank() -> None:
    repository = FakeRepository()
    providers = FakeProviders()
    engine = RecallEngine(repository, providers, HindsightOptions())

    result = await engine.recall("simple fact", mode="fast")

    assert repository.calls["graph"] == 0
    assert repository.calls["temporal"] == 0
    assert providers.json_calls == []
    assert result.trace["phase_ms"]["query_analysis_llm"] == 0
    assert result.trace["phase_ms"]["neural_rerank_llm"] == 0
    assert result.trace["algorithm"] == "semantic+BM25/RRF/MMR"


async def test_recall_validates_mode_and_top_k() -> None:
    engine = RecallEngine(FakeRepository(), FakeProviders(), HindsightOptions())

    with pytest.raises(ValueError, match="unsupported retrieval mode"):
        await engine.recall("query", mode="turbo")
    with pytest.raises(ValueError, match="top_k"):
        await engine.recall("query", top_k=0)


async def test_conversation_filter_is_applied_before_all_arm_rankings() -> None:
    class SourceFilteringRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.file = candidate("file", "high ranking file", semantic=1.0)
            self.file.source_type = "graphrag-pipeline"
            # Semantic-only memory below the public floor: it must clear the
            # gates via the conversation floor, not via a keyword hit.
            self.conversation = candidate(
                "conversation", "remembered preference", semantic=0.3
            )
            self.conversation.source_type = "conversation"

        async def semantic_search(
            self, embedding, limit, *, source_type=None, filters=None
        ):
            self.calls["semantic"] += 1
            self.source_filters.append(source_type)
            rows = [self.file, self.conversation]
            return [item for item in rows if item.source_type == source_type]

        async def keyword_search(self, query, limit, *, source_type=None, filters=None):
            self.calls["keyword"] += 1
            self.source_filters.append(source_type)
            return [] if source_type == "conversation" else [self.file]

    repository = SourceFilteringRepository()
    result = await RecallEngine(repository, FakeProviders(), HindsightOptions()).recall(
        "preference", mode="fast", source_type="conversation"
    )

    assert [item.id for item in result.results] == ["conversation"]
    assert repository.source_filters == ["conversation", "conversation"]
    assert result.trace["source_type"] == "conversation"


async def test_deep_recall_drops_irrelevant_chunk_despite_high_reranker_score() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-noise", "unrelated noise", semantic=0.1)
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("does not exist in kb")

    assert [item.id for item in result.results] == ["memory-b"]
    assert result.trace["filtered_count"] == 1


class FixedRankingProviders(FakeProviders):
    """Rerank responder returning caller-fixed scores (fallback: default)."""

    def __init__(self, scores: dict[str, float]) -> None:
        super().__init__()
        self.scores = scores

    async def json(self, system: str, user: str, *, timeout: float = 600) -> dict:
        if system.startswith("Rank memories"):
            return {
                "ranking": [
                    {"id": memory_id, "score": score}
                    for memory_id, score in self.scores.items()
                ]
            }
        return await super().json(system, user, timeout=timeout)


class MemoryFloorRepository(FakeRepository):
    """A conversation memory at semantic 0.3: below the public floor, above
    the conversation floor. It never has a keyword score."""

    def __init__(self) -> None:
        super().__init__()
        self.memory = candidate("conversation-mem", "remembered preference", semantic=0.3)
        self.memory.source_type = "conversation"

    async def semantic_search(self, embedding, limit, *, source_type=None, filters=None):
        self.calls["semantic"] += 1
        self.source_filters.append(source_type)
        if source_type == "conversation":
            return [self.memory]
        return [self.memory, self.a, self.b]

    async def keyword_search(self, query, limit, *, source_type=None, filters=None):
        self.calls["keyword"] += 1
        self.source_filters.append(source_type)
        return [] if source_type == "conversation" else [self.b, self.a]


async def test_deep_mode_semantic_floor_applies_despite_passing_score_gate() -> None:
    # semantic 0.2 is below the 0.45 floor; the reranker score (0.8) would
    # clamp the final score to 0.45, above the 0.4 score gate — the floor
    # must still drop the candidate.
    repository = FakeRepository()
    repository.a = candidate("memory-low", "noise with a high reranker score", semantic=0.2)
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("noise with a high reranker score")

    assert [item.id for item in result.results] == ["memory-b"]
    assert result.trace["filtered_count"] == 1


async def test_deep_mode_drops_above_floor_but_below_score_gate() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-weak", "weak rerank result", semantic=0.6)
    engine = RecallEngine(
        repository, FixedRankingProviders({"memory-weak": 0.1}), HindsightOptions()
    )

    result = await engine.recall("weak rerank result")

    assert [item.id for item in result.results] == ["memory-b"]
    assert result.trace["filtered_count"] == 1


async def test_deep_mode_keeps_candidate_above_both_gates() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-strong", "solid match", semantic=0.6)
    engine = RecallEngine(
        repository, FixedRankingProviders({"memory-strong": 0.9}), HindsightOptions()
    )

    result = await engine.recall("solid match")

    ids = [item.id for item in result.results]
    assert "memory-strong" in ids
    assert "memory-b" in ids


async def test_fast_mode_keeps_keyword_hit_below_semantic_floor() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-hit", "exact term match", semantic=0.1, keyword=0.9)
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("exact term", mode="fast")

    assert "memory-hit" in [item.id for item in result.results]


async def test_memory_recall_uses_lower_floor_than_public_recall() -> None:
    engine = RecallEngine(MemoryFloorRepository(), FakeProviders(), HindsightOptions())

    public = await engine.recall("remembered preference", mode="fast")
    assert "conversation-mem" not in [item.id for item in public.results]

    memory = await engine.recall(
        "remembered preference", mode="fast", source_type="conversation"
    )
    assert [item.id for item in memory.results] == ["conversation-mem"]


async def test_public_and_memory_floors_are_independently_configurable() -> None:
    raised = RecallEngine(
        MemoryFloorRepository(), FakeProviders(), HindsightOptions(
            recall_min_semantic=0.9
        )
    )
    memory = await raised.recall(
        "remembered preference", mode="fast", source_type="conversation"
    )
    assert [item.id for item in memory.results] == ["conversation-mem"]

    lowered = RecallEngine(
        MemoryFloorRepository(), FakeProviders(), HindsightOptions(
            conversation_recall_min_semantic=0.05
        )
    )
    public = await lowered.recall("remembered preference", mode="fast")
    assert "conversation-mem" not in [item.id for item in public.results]


async def test_fast_recall_drops_low_similarity_without_keyword_hit() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-noisy", "noise without keywords", semantic=0.2)
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("unknown term", mode="fast")

    assert [item.id for item in result.results] == ["memory-b"]
    assert result.trace["filtered_count"] == 1


async def test_deep_recall_keeps_bm25_hit_even_with_low_similarity() -> None:
    repository = FakeRepository()
    repository.b = candidate(
        "memory-hit", "exact term match", semantic=0.1, keyword=0.9
    )
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("exact term")

    hit = next(item for item in result.results if item.id == "memory-hit")
    # BM25 命中不受语义钳制：semantic=0.1 若被钳制会变成 0.35
    assert hit.reranker_score is not None
    assert hit.final_score == pytest.approx(hit.reranker_score)
    assert hit.final_score > 0.35


async def test_recall_returns_empty_result_when_nothing_is_relevant() -> None:
    repository = FakeRepository()
    repository.a = candidate("memory-x", "noise one", semantic=0.1)
    repository.b = candidate("memory-y", "noise two", semantic=0.05)
    engine = RecallEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.recall("totally unknown")

    assert result.results == []
    assert result.chunks == {}
    assert result.trace["selected_count"] == 0
    assert result.trace["filtered_count"] == 2


async def test_request_filters_and_budgets_apply_to_every_fast_arm() -> None:
    class Repository(FakeRepository):
        seen = []

        async def semantic_search(self, _embedding, limit, **kwargs):
            self.seen.append(("semantic", limit, kwargs["filters"]))
            self.a.memory_type = "observation"
            return [self.a]

        async def keyword_search(self, _query, limit, **kwargs):
            self.seen.append(("keyword", limit, kwargs["filters"]))
            return [self.a]

        async def recall_details(self, _ids, *, include_source_facts=False):
            return {
                self.a.id: {
                    "freshness": "stale",
                    "stale_reason": "source replaced",
                    "updated_at": "2026-09-09T00:00:00+00:00",
                    "source_facts": [{"id": "current", "text": "current fact"}],
                }
            }

    repository = Repository()
    filters = RecallFilter(
        memory_types=("observation",),
        source_types=("conversation",),
        min_scores={"semantic": 0.5},
        prefer_observations=True,
        include=("documents", "source_facts"),
        include_stale=True,
        timeout_seconds=2,
        max_tokens=8,
        max_candidates=2,
    )
    result = await RecallEngine(
        repository,
        FakeProviders(),
        HindsightOptions(recall_max_results=2, recall_max_candidates=3),
    ).recall("Alice", mode="fast", top_k=999, filters=filters)

    assert [(name, limit) for name, limit, _ in repository.seen] == [
        ("keyword", 2),
        ("semantic", 2),
    ]
    assert all(seen is filters for _, _, seen in repository.seen)
    assert result.trace["requested_top_k"] == 999
    assert result.trace["effective_top_k"] == 2
    assert result.trace["token_budget"] == 8
    assert result.results[0].freshness == "stale"
    assert result.results[0].stale_reason == "source replaced"
    assert result.results[0].metadata["source_facts"][0]["id"] == "current"
    assert result.chunks == {}
    assert result.entities == {}
    assert result.documents[result.results[0].document_id]["title"]


def test_recall_filter_rejects_invalid_budget_score_and_time() -> None:
    from datetime import datetime

    with pytest.raises(ValueError, match="minimum scores"):
        RecallFilter(min_scores={"semantic": 1.1})
    with pytest.raises(ValueError, match="timezone"):
        RecallFilter(reference_time=datetime(2026, 9, 9))
    with pytest.raises(ValueError, match="max_tokens"):
        RecallFilter(max_tokens=0)
