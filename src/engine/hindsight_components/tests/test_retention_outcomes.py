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


def test_tolerant_parse_remaps_caused_by_to_surviving_positions() -> None:
    payload = {
        "facts": [
            {"text": "invalid fact", "type": "nonsense"},
            {"text": "Alice visited Oslo in May.", "type": "world"},
            {
                "text": "She flew there for a conference.",
                "type": "world",
                "caused_by": [1],
            },
        ]
    }

    facts, rejected = RetainEngine._parse_facts_tolerant(payload, None)

    assert len(rejected) == 1
    assert [fact.text for fact in facts] == [
        "Alice visited Oslo in May.",
        "She flew there for a conference.",
    ]
    # The link pointed at original index 1 (the visit); after the sibling
    # rejection it must still point at the visit, now survivor position 0.
    assert facts[1].caused_by == [0]


def test_tolerant_parse_drops_links_that_pointed_at_rejected_facts() -> None:
    payload = {
        "facts": [
            {"text": "invalid fact", "type": "nonsense"},
            {"text": "Alice visited Oslo in May.", "type": "world"},
            {
                "text": "She flew there for a conference.",
                "type": "world",
                "caused_by": [0],
            },
        ]
    }

    facts, rejected = RetainEngine._parse_facts_tolerant(payload, None)

    assert len(rejected) == 1
    # The link pointed at the rejected sibling: dropped, never re-pointed
    # at the surviving fact that took over its position.
    assert facts[1].caused_by == []


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
        ({"facts": [{"text": "fact", "type": "invalid"}]}, "partial"),
        ({"facts": [{"text": "fact", "occurred_start": "bad-date"}]}, "partial"),
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


async def test_invalid_fact_degrades_chunk_but_preserves_valid_sibling(caplog):
    provider = ExtractionProvider(
        {
            "facts": [
                {"text": "The valid fact", "type": "world", "confidence": 0.9},
                {
                    "text": "sensitive malformed fact",
                    "type": "not-a-type",
                },
            ]
        }
    )
    repository = FakeRepository()

    with caplog.at_level("WARNING"):
        result = await RetainEngine(repository, provider, HindsightOptions()).retain(
            RetainInput(
                document_id="doc-safe-id",
                title="source",
                content="Original source text",
                file_type="text",
            )
        )

    assert result.status == "partial"
    assert result.facts == 1
    assert result.error_code == "invalid_fact_type"
    assert repository.plan.error_code == "invalid_fact_type"
    assert any(memory.text == "The valid fact" for memory in repository.plan.memories)
    assert "error_code=invalid_fact_type" in caplog.text
    assert "sensitive malformed fact" not in caplog.text


async def test_provider_failure_uses_sanitized_error_code(caplog):
    repository = FakeRepository()

    with caplog.at_level("WARNING"):
        result = await RetainEngine(
            repository,
            ExtractionProvider(RuntimeError("secret provider response")),
            HindsightOptions(),
        ).retain(
            RetainInput(
                document_id="doc-safe-id",
                title="source",
                content="Original source text",
                file_type="text",
            )
        )

    assert result.error_code == "provider_runtimeerror"
    assert "error_code=provider_runtimeerror" in caplog.text
    assert "secret provider response" not in caplog.text


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
