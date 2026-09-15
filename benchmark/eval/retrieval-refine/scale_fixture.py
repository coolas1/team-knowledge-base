"""Generate a deterministic production-shaped retrieval scale manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import random


def build_manifest(records: int, seed: int = 9457) -> dict:
    if records < 30_000:
        raise ValueError("scale acceptance requires at least 30,000 records")
    rng = random.Random(seed)
    source_counts = {"upload": 0, "conversation": 0}
    length_buckets = {"short": 0, "medium": 0, "long": 0}
    rows = []
    for index in range(records):
        source = "conversation" if rng.random() < 0.72 else "upload"
        bucket = rng.choices(("short", "medium", "long"), (0.55, 0.35, 0.10))[0]
        source_counts[source] += 1
        length_buckets[bucket] += 1
        rows.append((index, source, bucket, rng.randrange(1, 10_000)))
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "seed": seed,
        "records": records,
        "source_counts": source_counts,
        "length_buckets": length_buckets,
        "fixture_digest": digest,
        "indexed_candidate_bound": 300,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=30_000)
    parser.add_argument("--seed", type=int, default=9457)
    args = parser.parse_args()
    print(json.dumps(build_manifest(args.records, args.seed), sort_keys=True))


if __name__ == "__main__":
    main()
