from datetime import datetime, timezone
import json
import pytest

from src.engine.components.chunker import Chunk
from src.engine.hindsight_components.retention_snapshot import (
    content_snapshot,
    snapshot_chunks,
)
from src.engine.hindsight_components.types import RetainInput


def test_snapshot_roundtrip_keeps_source_time_and_updates_policy():
    original = RetainInput(
        document_id="doc",
        title="Original",
        content="Yesterday I shipped.",
        file_type="txt",
        source_timestamp=datetime(2020, 1, 2, tzinfo=timezone.utc),
        reference_timezone="Asia/Shanghai",
        speakers={"user": "alice"},
        metadata={"origin": ["first"]},
        request_id="request",
        expected_revision=3,
    )
    snapshot = content_snapshot(original, [Chunk(0, original.content, 10)])
    original.metadata["origin"].append("later")
    chunks, sources = snapshot_chunks(
        json.loads(json.dumps(snapshot)), policy_version=2
    )
    assert chunks == [Chunk(0, original.content, 10)]
    restored = sources[0]
    assert restored.source_timestamp == original.source_timestamp
    assert restored.reference_timezone == "Asia/Shanghai"
    assert restored.speakers == {"user": "alice"}
    assert restored.metadata == {"origin": ["first"]}
    assert restored.policy_version == 2
    assert restored.request_id is None
    assert restored.expected_revision is None


def test_empty_snapshot_is_distinct_from_missing_legacy_snapshot():
    source = RetainInput(document_id="doc", title="", content="", file_type="txt")
    snapshot = content_snapshot(source, [])
    assert snapshot["content"] == ""
    assert snapshot_chunks(snapshot, policy_version=1) == ([], [])


def test_append_requires_identity_and_default_hash_stays_compatible():
    from dataclasses import asdict, make_dataclass
    from src.engine.hindsight_components.request_identity import request_fingerprint

    values = dict(document_id="doc", title="", content="more", file_type="txt")
    with pytest.raises(ValueError, match="append requires"):
        RetainInput(**values, update_mode="append")
    with pytest.raises(ValueError, match="update_mode"):
        RetainInput(**values, update_mode="invalid")
    source = RetainInput(**values, request_id="stable")
    legacy_values = asdict(source)
    legacy_values.pop("update_mode")
    Legacy = make_dataclass("Legacy", list(legacy_values))
    assert request_fingerprint(source) == request_fingerprint(Legacy(**legacy_values))


def test_replace_keeps_blocks_but_does_not_match_inside_changed_sentences():
    from dataclasses import replace
    from src.engine.hindsight_components.retention_snapshot import replace_snapshot

    original = RetainInput(
        document_id="doc",
        title="",
        content="Old block\n\nKeep block",
        file_type="txt",
        speakers={"user": "alice"},
    )
    snapshot = content_snapshot(
        original, [Chunk(0, "Old block", 4), Chunk(1, "Keep block", 5)]
    )
    changed = replace(
        original, content="New block\n\nKeep block", speakers={"user": "bob"}
    )
    updated = replace_snapshot(changed, snapshot, chunk_size=500, overlap=50)
    chunks, sources = snapshot_chunks(updated, policy_version=2)
    assert [chunk.text for chunk in chunks] == ["New block", "Keep block"]
    assert [source.speakers["user"] for source in sources] == ["bob", "alice"]
    assert all(source.policy_version == 2 for source in sources)
    sentence = replace(changed, content="Keep block has changed")
    updated = replace_snapshot(sentence, snapshot, chunk_size=500, overlap=50)
    assert len(updated["chunks"]) == 1
    assert updated["chunks"][0]["source"]["speakers"]["user"] == "bob"


def test_exact_replace_keeps_append_boundaries_and_overlapping_chunks():
    from src.engine.hindsight_components.retention_snapshot import replace_snapshot

    original = RetainInput(
        document_id="doc", title="", content="A\nB\nC", file_type="txt"
    )
    snapshot = content_snapshot(original, [Chunk(0, "A\nB", 2), Chunk(1, "B\nC", 2)])
    updated = replace_snapshot(original, snapshot, chunk_size=500, overlap=50)
    assert updated == snapshot
    assert updated is not snapshot
