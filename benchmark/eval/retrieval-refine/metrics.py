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


def document_recall_at_k(
    cases: list[dict], results: dict[str, dict], *, k: int = 5
) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    scores = []
    for case in cases:
        expected = set(case.get("expected_document_ids", []))
        returned = set(results.get(case["id"], {}).get("document_ids", [])[:k])
        scores.append(len(expected & returned) / len(expected) if expected else 1.0)
    return sum(scores) / len(scores) if scores else 0.0


def passage_usefulness(cases: list[dict], results: dict[str, dict]) -> float:
    required = [case for case in cases if case.get("requires_passage")]
    if not required:
        return 1.0
    return sum(
        bool(results.get(case["id"], {}).get("passage_useful")) for case in required
    ) / len(required)


def metadata_disclosure_accuracy(cases: list[dict], results: dict[str, dict]) -> float:
    metadata_only = [case for case in cases if not case.get("requires_passage")]
    if not metadata_only:
        return 1.0
    return sum(
        results.get(case["id"], {}).get("metadata_only") is True
        and results.get(case["id"], {}).get("passage_useful") is False
        for case in metadata_only
    ) / len(metadata_only)


def honesty_rates(cases: list[dict], results: dict[str, dict]) -> dict[str, float]:
    if not cases:
        return {
            "false_positive_rate": 0.0,
            "unsupported_answer_rate": 0.0,
            "fabricated_citation_rate": 0.0,
        }
    negative = [case for case in cases if not case.get("expected_document_ids")]
    false_positives = sum(
        bool(results.get(case["id"], {}).get("document_ids")) for case in negative
    )
    unsupported = sum(
        results.get(case["id"], {}).get("answer_supported") is not True
        for case in cases
    )
    cited = 0
    fabricated = 0
    for case in cases:
        result = results.get(case["id"], {})
        returned = set(result.get("document_ids", []))
        citations = result.get("citation_ids", [])
        cited += len(citations)
        fabricated += sum(identity not in returned for identity in citations)
    return {
        "false_positive_rate": false_positives / len(negative) if negative else 0.0,
        "unsupported_answer_rate": unsupported / len(cases),
        "fabricated_citation_rate": fabricated / cited if cited else 0.0,
    }
