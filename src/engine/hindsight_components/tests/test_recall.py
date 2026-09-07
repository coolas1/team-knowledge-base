from __future__ import annotations

import pytest

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.recall import RecallEngine

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
            self.conversation = candidate(
                "conversation", "remembered preference", semantic=0.2
            )
            self.conversation.source_type = "conversation"

        async def semantic_search(
            self, embedding, limit, *, source_type=None
        ):
            self.calls["semantic"] += 1
            self.source_filters.append(source_type)
            rows = [self.file, self.conversation]
            return [item for item in rows if item.source_type == source_type]

        async def keyword_search(self, query, limit, *, source_type=None):
            self.calls["keyword"] += 1
            self.source_filters.append(source_type)
            return [self.conversation] if source_type == "conversation" else [self.file]

    repository = SourceFilteringRepository()
    result = await RecallEngine(
        repository, FakeProviders(), HindsightOptions()
    ).recall("preference", mode="fast", source_type="conversation")

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
