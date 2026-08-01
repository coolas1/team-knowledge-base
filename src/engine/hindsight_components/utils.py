"""Small algorithm helpers shared by the Hindsight core."""

from __future__ import annotations

import math
import re
import uuid
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
    return re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).strip()


def lexical_tokens(value: str) -> list[str]:
    normalized = value.casefold()
    words = re.findall(r"[a-z0-9_]+", normalized)
    for run in re.findall(r"[\u3040-\u30ff\u3400-\u9fff]+", normalized):
        words.extend(run[index : index + 2] for index in range(max(1, len(run) - 1)))
    return words


def document_lock_key(document_id: uuid.UUID) -> int:
    value = document_id.int & ((1 << 64) - 1)
    return value - (1 << 64) if value >= (1 << 63) else value


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
