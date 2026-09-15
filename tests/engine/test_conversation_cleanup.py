from __future__ import annotations

import uuid
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.engine.conversation_cleanup import (
    classify_conversation_memories,
    export_cleanup_manifest,
    load_cleanup_manifest,
    validate_restoration_preconditions,
)


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _row(kind: str, **overrides):
    values = {
        "id": uuid.uuid5(uuid.NAMESPACE_URL, kind),
        "document_id": uuid.uuid5(uuid.NAMESPACE_DNS, kind),
        "origin": "user",
        "authority": "user_stated",
        "confirmed_by_turn_id": None,
        "mentioned_at": NOW,
        "content_fingerprint": kind,
        "duplicate_of": None,
        "superseded_by": None,
        "lifecycle_state": "current",
        "expires_at": None,
        "state": "active",
        "memory_version": 1,
        "derived_from_evidence_ids": [],
        "source_memory_ids": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cleanup_manifest_classifies_every_state_without_source_text() -> None:
    duplicate_winner = _row(
        "duplicate-winner",
        content_fingerprint="same",
        authority="user_confirmed",
    )
    rows = [
        _row("keep"),
        duplicate_winner,
        _row("duplicate-loser", content_fingerprint="same"),
        _row("explicit-duplicate", duplicate_of=duplicate_winner.id),
        _row("superseded", superseded_by=uuid.uuid4()),
        _row("expired", expires_at=NOW - timedelta(seconds=1)),
        _row("assistant", origin="assistant", authority="assistant_derived"),
    ]

    manifest = classify_conversation_memories(
        rows, bank_id="bank-a", reference_time=NOW
    )

    assert manifest.counts == {
        "disallowed_assistant_derived": 1,
        "duplicate": 2,
        "expired": 1,
        "superseded": 1,
        "keep": 2,
        "unknown": 0,
    }
    assert set(manifest.retirement_ids) == {
        str(rows[index].id) for index in (2, 3, 4, 5, 6)
    }
    serialized = manifest.as_dict()
    assert "text" not in str(serialized)
    assert len(serialized["checksum"]) == 64


def test_unknown_provenance_blocks_all_automatic_retirement() -> None:
    unknown = _row(
        "unknown",
        origin="unknown",
        authority="unclassified",
        duplicate_of=uuid.uuid4(),
    )
    manifest = classify_conversation_memories(
        [unknown, _row("expired", expires_at=NOW - timedelta(days=1))],
        bank_id="bank-a",
        reference_time=NOW,
    )

    assert manifest.counts["unknown"] == 1
    assert any(item.classification == "unknown" for item in manifest.items)
    with pytest.raises(ValueError, match="unknown provenance"):
        _ = manifest.retirement_ids


def test_confirmed_assistant_commitment_is_kept() -> None:
    manifest = classify_conversation_memories(
        [
            _row(
                "confirmed",
                origin="assistant",
                authority="user_confirmed",
                confirmed_by_turn_id="turn-1",
            )
        ],
        bank_id="bank-a",
        reference_time=NOW,
    )
    assert manifest.counts["keep"] == 1


def test_export_integrity_and_restoration_preconditions(tmp_path) -> None:
    duplicate = _row(
        "duplicate", duplicate_of=uuid.uuid4(), source_memory_ids=[uuid.uuid4()]
    )
    manifest = classify_conversation_memories(
        [duplicate], bank_id="bank-a", reference_time=NOW
    )
    path = tmp_path / "cleanup.json"
    export_cleanup_manifest(manifest, path)
    exported = path.read_text(encoding="utf-8")
    assert "conversation body" not in exported
    assert "metadata_json" not in exported
    restored_manifest = load_cleanup_manifest(path, expected_bank_id="bank-a")
    assert restored_manifest == manifest

    retired = _row(
        "duplicate",
        id=duplicate.id,
        document_id=duplicate.document_id,
        duplicate_of=duplicate.duplicate_of,
        source_memory_ids=duplicate.source_memory_ids,
        state="retired",
        memory_version=2,
    )
    validate_restoration_preconditions(manifest, [retired])
    retired.memory_version = 3
    with pytest.raises(ValueError, match="preconditions changed"):
        validate_restoration_preconditions(manifest, [retired])

    payload = json.loads(exported)
    payload["counts"]["duplicate"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_cleanup_manifest(path, expected_bank_id="bank-a")
