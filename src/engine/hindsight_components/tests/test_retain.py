from __future__ import annotations

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.types import RetainInput

from src.engine.hindsight_components.tests.fakes import FakeProviders, FakeRepository


async def test_repeated_extraction_has_stable_fact_and_chunk_references():
    repository = FakeRepository()
    engine = RetainEngine(repository, FakeProviders(), HindsightOptions())
    value = RetainInput(
        document_id="stable-doc",
        title="source",
        content="Alice ran a survey",
        file_type="text",
    )
    await engine.retain(value)
    first = {
        m.text: (m.id, m.metadata.get("chunk_id")) for m in repository.plan.memories
    }
    await engine.retain(value)
    second = {
        m.text: (m.id, m.metadata.get("chunk_id")) for m in repository.plan.memories
    }
    assert first == second
    assert all(chunk_id for _, chunk_id in first.values() if chunk_id is not None)


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


async def test_retain_skips_empty_content_without_raising() -> None:
    # Image-only docs (OCR returns no text) legitimately have nothing to
    # remember: persist an empty plan so the document reaches the "indexed"
    # terminal state with zero memories instead of erroring.
    repository = FakeRepository()
    engine = RetainEngine(repository, FakeProviders(), HindsightOptions())

    result = await engine.retain(
        RetainInput(
            document_id="document-1",
            title="photo.png",
            content="   ",
            file_type="image",
        )
    )

    assert result.chunks == 0
    assert result.facts == 0
    assert result.observations == 0
    assert result.memories == 0
    assert result.links == 0
    assert repository.plan is not None
    assert repository.plan.memories == []
    assert repository.plan.links == []


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
