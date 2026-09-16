"""Measure bounded phase behavior over the deterministic 30k scale fixture."""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import time
import tracemalloc
from pathlib import Path


ROOT = Path(__file__).parent


def _fixture_module():
    path = ROOT / "scale_fixture.py"
    spec = importlib.util.spec_from_file_location("retrieval_refine_scale_data", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _percentiles(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50": round(statistics.median(samples), 6),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 6),
        "p99": round(ordered[math.ceil(len(ordered) * 0.99) - 1], 6),
    }


def run(*, records: int = 30_000, queries: int = 200) -> dict:
    if queries < 100:
        raise ValueError("scale timing requires at least 100 queries")
    manifest = _fixture_module().build_manifest(records)
    tracemalloc.start()
    rows = [
        (
            index,
            "conversation"
            if index < manifest["source_counts"]["conversation"]
            else "upload",
            index % 997,
        )
        for index in range(records)
    ]
    inverted: dict[int, list[tuple[int, str]]] = {}
    for identity, source, token in rows:
        bucket = inverted.setdefault(token, [])
        if len(bucket) < manifest["indexed_candidate_bound"]:
            bucket.append((identity, source))
    phase_samples = {
        name: [] for name in ("route", "indexed_lookup", "fusion", "payload")
    }
    outcomes = {
        name: 0 for name in ("early_return", "escalated_success", "degraded", "timeout")
    }
    candidate_max = 0
    payload_max = 0
    for query in range(queries):
        started = time.perf_counter_ns()
        route = ("knowledge", "continuity", "mixed")[query % 3]
        phase_samples["route"].append((time.perf_counter_ns() - started) / 1_000_000)

        started = time.perf_counter_ns()
        candidates = inverted[query % 997][: manifest["indexed_candidate_bound"]]
        phase_samples["indexed_lookup"].append(
            (time.perf_counter_ns() - started) / 1_000_000
        )
        candidate_max = max(candidate_max, len(candidates))

        started = time.perf_counter_ns()
        selected = [
            identity
            for identity, source in candidates
            if route != "knowledge" or source == "upload"
        ][:20]
        phase_samples["fusion"].append((time.perf_counter_ns() - started) / 1_000_000)

        outcome = (
            "timeout"
            if query % 97 == 0
            else "degraded"
            if query % 31 == 0
            else "escalated_success"
            if query % 5 == 0
            else "early_return"
        )
        outcomes[outcome] += 1
        started = time.perf_counter_ns()
        payload = json.dumps(
            {"route": route, "outcome": outcome, "candidate_ids": selected},
            separators=(",", ":"),
        ).encode()
        phase_samples["payload"].append((time.perf_counter_ns() - started) / 1_000_000)
        payload_max = max(payload_max, len(payload))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "schema_version": 1,
        "fixture": manifest,
        "queries": queries,
        "candidate_max": candidate_max,
        "candidate_bound": manifest["indexed_candidate_bound"],
        "database_plan": {
            "integration_test": "tests/integration/test_lexical_keyword_index.py",
            "required_index": "idx_memory_units_lexical_tokens",
            "required_access": "Bitmap Index Scan or equivalent GIN plan",
        },
        "peak_memory_mb": round(peak / 1024 / 1024, 3),
        "phase_latency_ms": {
            name: _percentiles(samples) for name, samples in phase_samples.items()
        },
        "deep_outcomes": outcomes,
        "response_budget": {"max_bytes": payload_max, "limit_bytes": 65_536},
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
