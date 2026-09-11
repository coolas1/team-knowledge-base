from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from src.engine.hindsight_components.fact_cache import FactCache, cache_key
from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.reflect import ReflectEngine
from src.engine.hindsight_components.types import RecallFilter
from src.engine.scope import MemoryScope, TagFilter


def fact(identity, text="预算 budget", **extra):
    return {"id": identity, "type": "world", "text": text, **extra}


def test_lru_ttl_disabled_and_scope_isolation():
    now = [0]
    cache = FactCache(2, 10, clock=lambda: now[0])
    cache.remember("a", [fact("1"), fact("2")])
    cache.remember("a", [fact("1")])
    cache.remember("b", [fact("3")])
    assert [
        f["id"] for f in cache.candidates("a", "预算", limit=8, max_tokens=100)
    ] == ["1"]
    now[0] = 10
    assert cache.candidates("a", "预算", limit=8, max_tokens=100) == []
    cache = FactCache(0)
    cache.remember("a", [fact("1")])
    assert not cache._entries


def test_fact_selection_relevance_budget_and_copy():
    cache = FactCache()
    cache.remember("a", [fact("1"), fact("2", "unrelated"), fact("3", "budget " * 500)])
    selected = cache.candidates("a", "budget", limit=8, max_tokens=20)
    assert [f["id"] for f in selected] == ["1"]
    selected[0]["text"] = "mutated"
    assert (
        cache.candidates("a", "budget", limit=8, max_tokens=20)[0]["text"]
        == "预算 budget"
    )


async def test_warm_fact_avoids_search_and_revalidates():
    cache = FactCache()
    cached = fact("1", updated_at="v1")
    repo = SimpleNamespace(scope="bank-a")
    repo.expand_memory_record = AsyncMock(
        return_value={"memory": cached, "freshness": "active"}
    )
    providers = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "tool": "done",
                "answer": "budget",
                "citations": [{"type": "memory", "id": "1"}],
            }
        )
    )
    recall = SimpleNamespace(recall=AsyncMock())
    engine = ReflectEngine(
        recall,
        repo,
        providers,
        HindsightOptions(adaptive_reflect_enabled=True),
        fact_cache=cache,
    )
    key = cache_key(repo.scope, RecallFilter())
    cache.remember(key, [cached])
    result = await engine.reflect("budget")
    assert result.text == "budget"
    assert "预算 budget" in providers.json.call_args.args[1]
    recall.recall.assert_not_called()
    assert result.tool_trace[0]["tool"] == "fact_cache"
    repo.expand_memory_record.return_value = None
    await engine.reflect("budget")
    assert not cache.candidates(key, "budget", limit=8, max_tokens=100)


async def test_vector_pipeline_embeds_all_chunks_without_entity_calls(monkeypatch):
    from src.engine.graphrag import pipeline
    from src.engine.components.analyzer import AnalysisResult

    analyzer = SimpleNamespace(
        summarize_document=AsyncMock(return_value=AnalysisResult(overview="summary")),
        analyze_chunk=AsyncMock(),
        analyze_overview=AsyncMock(),
    )
    embed = AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts])
    monkeypatch.setattr(pipeline, "embedder", SimpleNamespace(embed_batch=embed))
    pipe = pipeline.Pipeline(object(), analyzer=analyzer, vector_only=True)
    overview, chunks, analyses, embeddings = await pipe._analyze_document(
        "\n\n".join("正文内容。" * 300 for _ in range(10)), "title", uuid4()
    )
    assert overview.overview == "summary"
    assert len(chunks) == len(embeddings) > 1
    assert not analyses
    analyzer.analyze_chunk.assert_not_called()
    analyzer.analyze_overview.assert_not_called()
    analyzer.summarize_document.assert_awaited_once()


def test_normalized_cache_scope_and_idle_renewal():
    a = MemoryScope(bank_id="a", visibility=TagFilter(("x", "y"), "all"))
    b = MemoryScope(bank_id="a", visibility=TagFilter(("y", "x"), "all"))
    assert cache_key(a, RecallFilter(timeout_seconds=1)) == cache_key(
        b, RecallFilter(timeout_seconds=30)
    )
    assert cache_key(a) != cache_key(MemoryScope(bank_id="b"))
    assert cache_key(a, RecallFilter(source_types=("conversation",))) != cache_key(a)
    now = [0]
    cache = FactCache(1, 10, clock=lambda: now[0])
    cache.remember("a", [fact("1")])
    now[0] = 9
    cache.remember("a", [fact("1")])  # valid use renews TTL
    now[0] = 11
    assert cache.candidates("a", "budget", limit=1, max_tokens=50)
    cache.remember("a", [fact("2")])
    assert cache.stats()["evicted"] == 1
    now[0] = 22
    assert not cache.candidates("a", "budget", limit=1, max_tokens=50)
    assert cache.stats()["expired"] == 1


async def test_followup_reuses_fact_without_repeating_retrieval():
    import json
    from src.engine.hindsight_components.types import RecallCandidate, RecallResult

    item = RecallCandidate(
        id="1",
        document_id="doc",
        title="budget",
        text="budget is 100",
        source_text="budget is 100",
        chunk_index=0,
    )
    repo = SimpleNamespace(
        scope=MemoryScope(bank_id="a"),
        load_cached_facts=AsyncMock(return_value={"1": item.as_evidence()}),
        expand_memory_record=AsyncMock(
            return_value={"memory": item.as_evidence(), "freshness": "active"}
        ),
    )
    recall = SimpleNamespace(
        recall=AsyncMock(return_value=RecallResult([item], {}, {}, {}))
    )

    async def plan(system, prompt, **kwargs):
        if json.loads(prompt)["retrieved_memory_ids"]:
            return {
                "tool": "done",
                "answer": "100",
                "citations": [{"type": "memory", "id": "1"}],
            }
        return {"tool": "recall", "query": "budget"}

    provider = SimpleNamespace(json=AsyncMock(side_effect=plan))
    cache = FactCache()
    engine = ReflectEngine(
        recall,
        repo,
        provider,
        HindsightOptions(adaptive_reflect_enabled=True),
        fact_cache=cache,
    )
    assert (await engine.reflect("budget")).text == "100"
    assert recall.recall.await_count == 1
    assert (await engine.reflect("budget details")).text == "100"
    assert recall.recall.await_count == 1
    assert cache.stats()["hits"] == 1


async def test_bulk_validation_failure_falls_back_to_normal_retrieval():
    from src.engine.hindsight_components.tests.fakes import (
        FakeProviders,
        FakeRepository,
    )

    repo = FakeRepository()
    repo.scope = MemoryScope()
    repo.load_cached_facts = AsyncMock(side_effect=TimeoutError())
    cache = FactCache()
    cache.remember(cache_key(repo.scope), [fact("1")])
    provider = FakeProviders()
    engine = ReflectEngine(
        SimpleNamespace(recall=AsyncMock()),
        repo,
        provider,
        HindsightOptions(adaptive_reflect_enabled=True),
        fact_cache=cache,
    )
    result = await engine.reflect("budget")
    assert not any(row.get("tool") == "fact_cache" for row in result.tool_trace)
