"""Stable identities for durable memory content."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def canonical_memory_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def memory_content_fingerprint(text: str, memory_type: str, origin: str) -> str:
    canonical = canonical_memory_text(text)
    return hashlib.sha256(
        f"{memory_type.strip().casefold()}\0{origin.strip().casefold()}\0{canonical}".encode()
    ).hexdigest()
