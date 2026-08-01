"""Small algorithm helpers shared by the Hindsight core."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def normalize_entity(value: str) -> str:
    return " ".join(value.casefold().split())


def cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(
        sum(x * x for x in right)
    )
    if denominator == 0:
        return 0.0
    return sum(x * y for x, y in zip(left, right, strict=True)) / denominator


def estimate_tokens(text: str) -> int:
    ascii_count = sum(character.isascii() for character in text)
    return max(1, ascii_count // 4 + len(text) - ascii_count)


def valid_indexes(values: Any, count: int) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in values if isinstance(values, list) else []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in seen:
            seen.add(index)
            result.append(index)
    return result
