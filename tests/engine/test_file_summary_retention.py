from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from src.engine.components.chunker import chunk_text
from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.retention_snapshot import content_snapshot
from src.engine.hindsight_components.tests.fakes import FakeProviders, FakeRepository
from src.engine.hindsight_components.types import RetainInput


@pytest.mark.parametrize(
    "source_type", ["upload", "graphrag-pipeline", "historical-backfill"]
)
@pytest.mark.parametrize("replay", [False, True])
async def test_every_file_entrypoint_replaces_legacy_full_text_snapshot(
    source_type, replay
):
    source = RetainInput(
        document_id="doc",
        title="file",
        content="legacy full text",
        file_type="markdown",
        source_type=source_type,
    )
    repo = FakeRepository()
    repo.retention_content_snapshot = AsyncMock(
        return_value=(1, content_snapshot(source, chunk_text(source.content)))
    )
    prepared = replace(
        source, content="summary only", metadata={"file_summary": {"key": "v1"}}
    )
    repo.prepare_file_retention = AsyncMock(return_value=prepared)
    engine = RetainEngine(
        repo, FakeProviders(), HindsightOptions(file_summary_enabled=True)
    )
    await engine.retain(source, replay_snapshot=replay)
    assert repo.plan.content_snapshot["content"] == "summary only"
    assert all(
        item["source"]["metadata"]["file_summary"]["key"] == "v1"
        for item in repo.plan.content_snapshot["chunks"]
    )
    assert repo.plan.source_context["file_summary"]["key"] == "v1"


async def test_migrated_snapshot_keeps_summary_policy_when_default_disabled():
    source = RetainInput(
        document_id="doc", title="file", content="raw text", file_type="markdown"
    )
    prepared = replace(
        source, content="summary", metadata={"file_summary": {"key": "v1"}}
    )
    repo = FakeRepository()
    repo.retention_content_snapshot = AsyncMock(
        return_value=(1, content_snapshot(prepared, chunk_text(prepared.content)))
    )
    repo.prepare_file_retention = AsyncMock(return_value=prepared)
    await RetainEngine(repo, FakeProviders(), HindsightOptions()).retain(source)
    repo.prepare_file_retention.assert_awaited_once()
    assert repo.plan.content_snapshot["content"] == "summary"


@pytest.mark.parametrize(
    "file_type,enabled", [("conversation", True), ("markdown", False)]
)
async def test_conversation_and_explicit_legacy_mode_keep_original_input(
    file_type, enabled
):
    source = RetainInput(
        document_id="doc", title="file", content="original", file_type=file_type
    )
    repo = FakeRepository()
    repo.prepare_file_retention = AsyncMock()
    await RetainEngine(
        repo, FakeProviders(), HindsightOptions(file_summary_enabled=enabled)
    ).retain(source)
    repo.prepare_file_retention.assert_not_awaited()
    assert repo.plan.content_snapshot["content"] == "original"


async def test_summary_failure_does_not_publish_placeholder_or_full_text():
    repo = FakeRepository()
    repo.prepare_file_retention = AsyncMock(side_effect=ValueError("summary failed"))
    source = RetainInput(
        document_id="doc", title="file", content="original", file_type="markdown"
    )
    with pytest.raises(ValueError, match="summary failed"):
        await RetainEngine(
            repo, FakeProviders(), HindsightOptions(file_summary_enabled=True)
        ).retain(source)
    assert repo.plan is None
