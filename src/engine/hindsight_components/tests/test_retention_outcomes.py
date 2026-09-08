from datetime import UTC, datetime
from dataclasses import replace

import pytest

from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.retention_context import fact_datetime
from src.engine.hindsight_components.tests.fakes import FakeProviders, FakeRepository
from src.engine.hindsight_components.types import RetainInput


class ExtractionProvider(FakeProviders):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    async def json(self, system, user):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


async def test_named_agent_and_source_time_reach_persisted_fact_metadata():
    provider = ExtractionProvider(
        {
            "facts": [
                {
                    "text": "The helper suggested a review",
                    "type": "experience",
                    "speaker_role": "assistant",
                    "modality": "suggested",
                    "occurred_start": "yesterday",
                }
            ]
        }
    )
    repository = FakeRepository()
    result = await RetainEngine(repository, provider, HindsightOptions()).retain(
        RetainInput(
            document_id="doc",
            title="chat",
            content="I suggest a review",
            file_type="conversation",
            agent_name="helper",
            source_timestamp=datetime(2024, 1, 1, 18, tzinfo=UTC),
            reference_timezone="Asia/Shanghai",
            policy_version=7,
        )
    )
    assert result.status == "success"
    fact = next(m for m in repository.plan.memories if not m.is_source_chunk)
    assert fact.metadata["speaker_id"] == "helper"
    assert fact.metadata["speakers"]["user"] is None
    assert fact.metadata["modality"] == "suggested"
    assert fact.metadata["policy_version"] == 7
    assert fact.occurred_start == datetime(2023, 12, 31, 16, tzinfo=UTC)


@pytest.mark.parametrize(
    "fact",
    [
        {"text": "Missing type"},
        {
            "text": "Invalid dates",
            "type": "world",
            "occurred_start": "2024-02-01",
            "occurred_end": "2024-01-01",
        },
    ],
)
def test_incomplete_schema_is_not_silently_classified_as_world(fact):
    with pytest.raises(ValueError):
        RetainEngine._parse_facts({"facts": [fact]})


async def test_embedding_failure_is_failed_and_does_not_replace_existing_memories():
    class BrokenEmbedding(FakeProviders):
        async def embed(self, *args, **kwargs):
            raise TimeoutError("embedding service unavailable")

    repository = FakeRepository()
    result = await RetainEngine(
        repository, BrokenEmbedding(), HindsightOptions()
    ).retain(
        RetainInput(
            document_id="doc",
            title="source",
            content="Alice wrote a report",
            file_type="text",
        )
    )
    assert result.status == "failed"
    assert result.stage_results["build"] == "failed"
    assert repository.plan is None


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"facts": []}, "empty"),
        (TimeoutError("provider unavailable"), "degraded"),
        ({"unexpected": []}, "degraded"),
        ({"facts": [{"text": "fact", "type": "invalid"}]}, "degraded"),
        ({"facts": [{"text": "fact", "occurred_start": "bad-date"}]}, "degraded"),
    ],
)
async def test_empty_and_failed_extractions_preserve_source_without_fabricating_facts(
    payload, status
):
    repository = FakeRepository()
    result = await RetainEngine(
        repository, ExtractionProvider(payload), HindsightOptions()
    ).retain(
        RetainInput(
            document_id="doc",
            title="source",
            content="Original source text",
            file_type="text",
        )
    )
    assert result.status == status
    assert result.facts == 0
    assert result.stage_results["extract"] == status
    assert repository.plan is not None
    assert len(repository.plan.memories) == 1
    assert repository.plan.memories[0].source_text == "Original source text"
    assert repository.plan.memories[0].metadata["extraction_status"] == status


def test_relative_dates_use_source_local_day_and_unknown_stays_unknown():
    source = RetainInput(
        document_id="doc",
        title="source",
        content="yesterday",
        file_type="text",
        source_timestamp=datetime(2024, 1, 1, 18, tzinfo=UTC),
        reference_timezone="Asia/Shanghai",
    )
    assert fact_datetime("yesterday", source) == datetime(2023, 12, 31, 16, tzinfo=UTC)
    assert fact_datetime("昨晚", source) == fact_datetime("yesterday", source)
    source = replace(source, source_timestamp=None)
    assert fact_datetime("yesterday", source) is None


@pytest.mark.parametrize(
    ("role", "kind", "modality"),
    [
        ("user", "world", "stated"),
        ("assistant", "experience", "suggested"),
        ("assistant", "experience", "completed"),
        ("unknown", "world", "unknown"),
    ],
)
def test_parser_keeps_speaker_and_action_distinctions(role, kind, modality):
    facts = RetainEngine._parse_facts(
        {
            "facts": [
                {
                    "text": "An attributed fact",
                    "type": kind,
                    "speaker_role": role,
                    "modality": modality,
                }
            ]
        }
    )
    assert (facts[0].speaker_role, facts[0].fact_type, facts[0].modality) == (
        role,
        kind,
        modality,
    )
