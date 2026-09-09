from __future__ import annotations

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.service import HindsightService

from .fakes import FakeProviders


class ExpansionRepository:
    async def expand_memory_record(self, memory_id):
        if memory_id == "missing":
            return None
        return {
            "memory": {"id": memory_id, "text": "summary"},
            "chunk": {"id": "chunk-1", "text": "abcdefghij"},
            "document": {"id": "doc-1", "text": "klmnopqrst"},
            "source_facts": [{"id": "fact-1", "text": "uvwxyz"}],
            "freshness": "stale",
            "stale_reason": "source replaced",
            "updated_at": "2026-09-09T00:00:00+00:00",
        }


async def test_expand_is_bounded_and_preserves_freshness() -> None:
    service = HindsightService(
        ExpansionRepository(), FakeProviders(), HindsightOptions(recall_max_tokens=4)
    )

    result = await service.expand_memory("memory-1", max_tokens=99)

    assert result is not None
    assert result.memory["freshness"] == "stale"
    assert result.memory["stale_reason"] == "source replaced"
    assert result.token_count <= 4
    assert result.truncated
    assert result.chunk["text"] == "abcdefghij"
    assert result.document["text"] == "klmnop"
    assert result.source_facts == ()


async def test_expand_returns_no_content_for_missing_reference() -> None:
    service = HindsightService(ExpansionRepository(), FakeProviders())
    assert await service.expand_memory("missing") is None
