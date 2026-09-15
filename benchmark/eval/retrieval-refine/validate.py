"""Validate the frozen retrieval-refinement fixture without external services."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from metrics import (
    conversation_precision_at_k,
    document_recall_at_k,
    metadata_disclosure_accuracy,
    passage_usefulness,
    route_accuracy,
)
from retention_replay import replay


ROOT = Path(__file__).parent
REQUIRED_RESULT_FIELDS = {
    "case_id",
    "source_types",
    "ranks",
    "scores",
    "citation_ids",
    "phase_ms",
    "payload_bytes",
    "outcome",
}


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def main() -> None:
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))
    assert cases["corpus_version"] == baseline["corpus_version"]
    case_ids = [item["id"] for item in cases["cases"]]
    result_ids = [item["case_id"] for item in baseline["results"]]
    assert len(case_ids) == len(set(case_ids)) and case_ids == result_ids
    assert set(baseline["active_memory_counts"]) == {"upload", "conversation"}
    for result in baseline["results"]:
        assert REQUIRED_RESULT_FIELDS <= result.keys()
        assert (
            len(result["source_types"]) == len(result["ranks"]) == len(result["scores"])
        )
        assert result["payload_bytes"] >= 0
    first = canonical_digest({"cases": cases, "baseline": baseline})
    second = canonical_digest({"cases": cases, "baseline": baseline})
    assert first == second
    continuity = json.loads(
        (ROOT / "continuity_cases.json").read_text(encoding="utf-8")
    )
    continuity_cases = continuity["cases"]
    fixture_results = {case["id"]: case["fixture_result"] for case in continuity_cases}
    assert (
        route_accuracy(continuity_cases, fixture_results)
        >= continuity["thresholds"]["route_accuracy"]
    )
    assert (
        conversation_precision_at_k(continuity_cases, fixture_results, k=3)
        >= continuity["thresholds"]["conversation_precision_at_3"]
    )
    metadata = json.loads((ROOT / "metadata_cases.json").read_text(encoding="utf-8"))
    metadata_cases = metadata["cases"]
    metadata_results = {case["id"]: case["fixture_result"] for case in metadata_cases}
    assert (
        document_recall_at_k(metadata_cases, metadata_results, k=5)
        >= metadata["thresholds"]["document_recall_at_5"]
    )
    assert (
        passage_usefulness(metadata_cases, metadata_results)
        >= metadata["thresholds"]["passage_usefulness"]
    )
    assert (
        metadata_disclosure_accuracy(metadata_cases, metadata_results)
        >= metadata["thresholds"]["metadata_disclosure_accuracy"]
    )
    retention = json.loads((ROOT / "retention_events.json").read_text(encoding="utf-8"))
    retention_records = replay(
        retention["events"], reference_time=retention["reference_time"]
    )
    active_ids = [
        record["id"] for record in retention_records if record["state"] == "active"
    ]
    assert active_ids == retention["expected"]["active_memory_ids"]
    print(
        json.dumps(
            {
                "status": "ok",
                "cases": len(case_ids),
                "continuity_cases": len(continuity_cases),
                "metadata_cases": len(metadata_cases),
                "retention_events": len(retention["events"]),
                "digest": first,
            }
        )
    )


if __name__ == "__main__":
    main()
