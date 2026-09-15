"""Run deterministic ranking ablations and print a JSON report."""

from __future__ import annotations

import json
import math
import statistics
import time
from pathlib import Path


ROOT = Path(__file__).parent
VARIANTS = (
    "current",
    "source_isolation",
    "source_local_fusion",
    "hierarchical",
    "safety_lane",
)


def rank(case: dict, variant: str) -> list[str]:
    candidates = list(case["candidates"])
    if variant != "current":
        candidates = [item for item in candidates if item["source"] == "upload"]
    if variant in {"hierarchical", "safety_lane"}:
        selected = [item for item in candidates if item["parent_selected"]]
        if variant == "safety_lane":
            selected.extend(
                item
                for item in candidates
                if not item["parent_selected"] and item["safety_score"] >= 0.8
            )
        candidates = list({item["id"]: item for item in selected}.values())

        def score(item):
            return (
                0.4 * item["parent_score"] + 0.6 * item["passage_score"]
                if item["passage_score"]
                else item["parent_score"]
            )

    elif variant == "source_local_fusion":

        def score(item):
            return 1 / (60 + item["semantic_rank"]) + 1 / (60 + item["keyword_rank"])

    else:

        def score(item):
            return 1 / item["semantic_rank"] + 1 / item["keyword_rank"]

    ordered = sorted(candidates, key=lambda item: (-score(item), item["id"]))
    return [item["document_id"] for item in ordered[:5]]


def metrics(cases: list[dict], rankings: dict[str, list[str]]) -> dict[str, float]:
    reciprocal, ndcg, recall = [], [], []
    covered = set()
    for case in cases:
        expected = set(case["expected_document_ids"])
        ranked = rankings[case["id"]]
        covered.update(ranked)
        positions = [index for index, item in enumerate(ranked, 1) if item in expected]
        reciprocal.append(1 / min(positions) if positions else 0.0)
        dcg = sum(1 / math.log2(index + 1) for index in positions)
        ideal = sum(1 / math.log2(index + 1) for index in range(1, len(expected) + 1))
        ndcg.append(dcg / ideal if ideal else 1.0)
        recall.append(len(expected.intersection(ranked)) / len(expected))
    return {
        "mrr": round(statistics.mean(reciprocal), 6),
        "ndcg_at_5": round(statistics.mean(ndcg), 6),
        "recall_at_5": round(statistics.mean(recall), 6),
        "unique_document_coverage": len(covered),
    }


def run(repetitions: int = 1000) -> dict:
    payload = json.loads((ROOT / "ablation_cases.json").read_text(encoding="utf-8"))
    results = {}
    for variant in VARIANTS:
        samples = []
        rankings = {}
        for _ in range(repetitions):
            started = time.perf_counter_ns()
            rankings = {case["id"]: rank(case, variant) for case in payload["cases"]}
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
        ordered = sorted(samples)
        results[variant] = {
            **metrics(payload["cases"], rankings),
            "latency_ms": {
                "p50": round(statistics.median(samples), 6),
                "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 6),
            },
        }
    selected = max(
        VARIANTS,
        key=lambda name: (
            results[name]["recall_at_5"],
            results[name]["ndcg_at_5"],
            results[name]["mrr"],
        ),
    )
    return {
        "schema_version": 1,
        "corpus_version": payload["corpus_version"],
        "repetitions": repetitions,
        "variants": results,
        "selected_configuration": selected,
    }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
