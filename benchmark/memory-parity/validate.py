"""Validate the labelled parity corpus without invoking models or services."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

CATEGORIES = {
    "attribution",
    "cross_turn",
    "duplicate",
    "change",
    "isolation",
    "time",
    "append",
    "delete",
    "failure",
    "model",
    "reasoning",
}


def validate(path: Path) -> dict:
    # Git may check out CRLF on Windows. Newline normalization keeps the
    # fingerprint stable across platforms without hiding content edits.
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    corpus = json.loads(raw)
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported corpus schema")
    cases = corpus["cases"]
    if len(cases) < 40:
        raise ValueError("at least 40 labelled cases are required")
    ids = set()
    counts = Counter()
    for case in cases:
        for key in ("id", "input", "query", "expected", "forbidden"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"missing nonempty {key}")
        if case["id"] in ids:
            raise ValueError(f"duplicate case: {case['id']}")
        ids.add(case["id"])
        categories = case.get("categories")
        if not isinstance(categories, list) or not categories:
            raise ValueError("categories must be a nonempty list")
        if not set(categories) <= CATEGORIES:
            raise ValueError("unknown category")
        counts.update(set(categories))
        if case.get("batch") not in {f"B{i}" for i in range(1, 8)}:
            raise ValueError("unknown batch")
    if any(counts[c] < 3 for c in CATEGORIES):
        raise ValueError("each category needs at least three cases")
    return {
        "cases": len(cases),
        "categories": dict(sorted(counts.items())),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


if __name__ == "__main__":
    print(json.dumps(validate(Path(__file__).with_name("cases.json")), indent=2))
