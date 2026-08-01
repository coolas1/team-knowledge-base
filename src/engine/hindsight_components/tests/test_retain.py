from __future__ import annotations

import pytest

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.retain import RetainEngine

from .fakes import FakeProviders, FakeRepository


async def test_retain_builds_atomic_memories_observation_and_links() -> None:
    repository = FakeRepository()
    engine = RetainEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.retain(
        document_id="document-1",
        title="week.md",
        content="Alice ran a survey and produced a report.",
        file_type="markdown",
    )

    assert result.chunks == 1
    assert result.facts == 2
    assert result.observations == 1
    assert repository.plan is not None
    assert len(repository.plan.memories) == 4
    observation = next(
        item for item in repository.plan.memories if item.memory_type == "observation"
    )
    assert len(observation.source_memory_ids) == 2
    link_types = {link.link_type for link in repository.plan.links}
    assert {"caused_by", "semantic", "temporal", "entity", "evidence"} <= link_types
    assert all(
        memory.document_id == "document-1" for memory in repository.plan.memories
    )


async def test_retain_rejects_empty_content() -> None:
    engine = RetainEngine(FakeRepository(), FakeProviders(), HindsightOptions())

    with pytest.raises(ValueError, match="empty"):
        await engine.retain(
            document_id="document-1",
            title="empty.md",
            content="  ",
            file_type="markdown",
        )
