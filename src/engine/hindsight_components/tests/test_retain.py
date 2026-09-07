from __future__ import annotations

import pytest

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.types import RetainInput

from .fakes import FakeProviders, FakeRepository


async def test_retain_builds_atomic_memories_observation_and_links() -> None:
    repository = FakeRepository()
    engine = RetainEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.retain(
        RetainInput(
            document_id="document-1",
            title="week.md",
            content="Alice ran a survey and produced a report.",
            file_type="markdown",
        )
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
            RetainInput(
                document_id="document-1",
                title="empty.md",
                content="  ",
                file_type="markdown",
            )
        )


async def test_conversation_retain_preserves_context_tags_and_provenance() -> None:
    repository = FakeRepository()
    providers = FakeProviders()
    engine = RetainEngine(repository, providers, HindsightOptions())
    transcript = "[user]\nI prefer concise answers.\n\n[assistant]\nUnderstood."

    await engine.retain(
        RetainInput(
            document_id="conversation-document",
            title="Conversation turn",
            content=transcript,
            file_type="conversation",
            source_type="conversation",
            context="A completed user and assistant turn",
            tags=("conversation", "session:session-1"),
            metadata={"session_id": "session-1", "turn_id": "turn-1"},
        )
    )

    assert repository.plan is not None
    for memory in repository.plan.memories:
        assert memory.context.startswith("A completed user and assistant turn") or (
            memory.context.startswith("Consolidated observation;")
        )
        assert {"conversation", "session:session-1"} <= set(memory.tags)
        assert memory.metadata["source_type"] == "conversation"
        assert memory.metadata["session_id"] == "session-1"
        assert memory.metadata["turn_id"] == "turn-1"
    extraction_prompt = providers.json_users[0]
    assert "SOURCE TYPE: conversation" in extraction_prompt
    assert "CONTEXT: A completed user and assistant turn" in extraction_prompt
    assert transcript in extraction_prompt
