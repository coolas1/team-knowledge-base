from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.engine.conversation_cleanup import classify_conversation_memories


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
