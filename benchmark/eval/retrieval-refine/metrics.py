"""Deterministic retrieval-refinement quality metrics."""

from __future__ import annotations


def route_accuracy(cases: list[dict], results: dict[str, dict]) -> float:
    if not cases:
        return 0.0
    correct = sum(
        results.get(case["id"], {}).get("route") == case["expected_route"]
        for case in cases
    )
    return correct / len(cases)


def conversation_precision_at_k(
    cases: list[dict], results: dict[str, dict], *, k: int = 3
) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    scored = []
    for case in cases:
        returned = results.get(case["id"], {}).get("conversation_ids", [])[:k]
        relevant = set(case.get("relevant_memory_ids", []))
        if not returned:
            scored.append(1.0 if not relevant else 0.0)
            continue
        scored.append(
            sum(identity in relevant for identity in returned) / len(returned)
        )
    return sum(scored) / len(scored) if scored else 0.0
