"""Canonical fingerprint of retain input, independent of ingestion time."""

from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID


def request_fingerprint(value) -> str:
    def encode(item):
        if isinstance(item, (datetime, UUID)):
            return item.isoformat() if isinstance(item, datetime) else str(item)
        raise TypeError("retain metadata must be JSON serializable")

    payload = asdict(value)
    # Preserve pre-append request hashes for the existing default operation.
    if payload.get("update_mode") == "replace":
        payload.pop("update_mode")
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=encode,
        ).encode()
    ).hexdigest()
