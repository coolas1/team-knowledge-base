from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent


def _replay_module():
    spec = importlib.util.spec_from_file_location(
        "retrieval_refine_retention", ROOT / "retention_replay.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retention_replay_blocks_contamination_and_preserves_provenance():
    payload = json.loads((ROOT / "retention_events.json").read_text(encoding="utf-8"))
    assert {event["scenario"] for event in payload["events"]} == {
        "repeated_document_answer",
        "tool_payload",
        "assistant_hallucination",
        "changed_preference",
        "duplicate_turn",
        "confirmed_plan",
        "expiry",
    }
    records = _replay_module().replay(
        payload["events"], reference_time=payload["reference_time"]
    )
    active = [record for record in records if record["state"] == "active"]
    assert [record["id"] for record in active] == payload["expected"][
        "active_memory_ids"
    ]
    assert len(active) == payload["expected"]["active_count"]
    assert {record["origin"] for record in active} == {
        payload["expected"]["required_origin"]
    }
    assert {record["authority"] for record in active} <= set(
        payload["expected"]["allowed_authorities"]
    )
    assert (
        next(record for record in records if record["id"] == "preference-old")["state"]
        == "superseded"
    )
    duplicate = next(
        record for record in records if record["id"] == "preference-duplicate"
    )
    assert duplicate["state"] == "duplicate"
    assert (
        next(record for record in records if record["id"] == "temporary-state")["state"]
        == "expired"
    )
    rejected = {record["id"] for record in records if record["state"] == "rejected"}
    assert {
        "document-answer-1",
        "document-answer-2",
        "tool-payload",
        "assistant-hallucination",
    } <= rejected
