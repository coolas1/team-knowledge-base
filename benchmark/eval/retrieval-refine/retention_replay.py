"""Deterministic selective-retention replay for contamination fixtures."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.engine.hindsight_components.memory_identity import (  # noqa: E402
    memory_content_fingerprint,
)
from src.engine.hindsight_components.retention_policy import RetentionPolicy  # noqa: E402


def replay(events: list[dict], *, reference_time: str) -> list[dict]:
    policy = RetentionPolicy()
    boundary = datetime.fromisoformat(reference_time)
    records: list[dict] = []
    for event in events:
        if event["source"] != "user":
            records.append(
                {
                    "id": event["id"],
                    "state": "rejected",
                    "reason": f"{event['source']}_source_disallowed",
                }
            )
            continue
        retained_types = policy.classify_user_text(event["text"])
        if not retained_types:
            records.append(
                {"id": event["id"], "state": "rejected", "reason": "policy_ineligible"}
            )
            continue
        fingerprint = memory_content_fingerprint(event["text"], "world", "user")
        duplicate = next(
            (
                record
                for record in records
                if record.get("fingerprint") == fingerprint
                and record["state"] == "active"
            ),
            None,
        )
        if duplicate:
            records.append(
                {
                    "id": event["id"],
                    "state": "duplicate",
                    "duplicate_of": duplicate["id"],
                    "fingerprint": fingerprint,
                    "origin": "user",
                    "authority": "user_stated",
                }
            )
            continue
        lifecycle_key = event.get("lifecycle_key")
        if lifecycle_key:
            for record in records:
                if (
                    record.get("lifecycle_key") == lifecycle_key
                    and record["state"] == "active"
                ):
                    record["state"] = "superseded"
                    record["superseded_by"] = event["id"]
        expires_at = (
            datetime.fromisoformat(event["expires_at"])
            if event.get("expires_at")
            else None
        )
        records.append(
            {
                "id": event["id"],
                "state": "expired"
                if expires_at is not None and expires_at <= boundary
                else "active",
                "fingerprint": fingerprint,
                "lifecycle_key": lifecycle_key,
                "origin": "user",
                "authority": (
                    "user_confirmed"
                    if event.get("confirmed_by_turn_id")
                    else "user_stated"
                ),
                "confirmed_by_turn_id": event.get("confirmed_by_turn_id"),
                "derived_from_evidence_ids": tuple(
                    event.get("derived_from_evidence_ids", [])
                ),
                "retained_types": retained_types,
            }
        )
    return records
