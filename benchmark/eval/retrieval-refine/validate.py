"""Validate the frozen retrieval-refinement fixture without external services."""

from __future__ import annotations

import hashlib
import asyncio
import importlib.util
import json
from pathlib import Path

from metrics import (
    conversation_precision_at_k,
    document_recall_at_k,
    honesty_rates,
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


def load_failure_runner():
    path = ROOT / "failure_injection.py"
    spec = importlib.util.spec_from_file_location("retrieval_failure_injection", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    honesty = json.loads((ROOT / "honesty_cases.json").read_text(encoding="utf-8"))
    honesty_cases = honesty["cases"]
    honesty_results = {case["id"]: case["fixture_result"] for case in honesty_cases}
    rates = honesty_rates(honesty_cases, honesty_results)
    assert all(
        rates[name] <= tolerance for name, tolerance in honesty["tolerances"].items()
    )
    ablation = json.loads((ROOT / "ablation_report.json").read_text(encoding="utf-8"))
    assert set(ablation["variants"]) == {
        "current",
        "source_isolation",
        "source_local_fusion",
        "hierarchical",
        "safety_lane",
    }
    assert ablation["selected_configuration"] == "safety_lane"
    scale = json.loads((ROOT / "scale_report.json").read_text(encoding="utf-8"))
    assert scale["fixture"]["records"] >= 30_000
    assert scale["candidate_max"] <= scale["candidate_bound"]
    assert (
        scale["response_budget"]["max_bytes"] <= scale["response_budget"]["limit_bytes"]
    )
    failures = asyncio.run(load_failure_runner().run_matrix())
    assert all(result["task_leaks"] == 0 for result in failures["results"])
    assert {"empty", "degraded", "timeout", "unavailable", "fallback"} <= {
        result["outcome"] for result in failures["results"]
    }
    replay_cases = json.loads((ROOT / "replay_cases.json").read_text(encoding="utf-8"))[
        "cases"
    ]
    assert {case["route"] for case in replay_cases} == {
        "knowledge",
        "conversation",
        "mixed",
    }
    autonomy = next(
        case for case in replay_cases if case["id"] == "automatic-driving-incident"
    )
    assert len(autonomy["document_ids"]) == 2
    assert set(autonomy["citations"]) == set(autonomy["document_ids"])
    print(
        json.dumps(
            {
                "status": "ok",
                "cases": len(case_ids),
                "continuity_cases": len(continuity_cases),
                "metadata_cases": len(metadata_cases),
                "retention_events": len(retention["events"]),
                "honesty_cases": len(honesty_cases),
                "ablation_variants": len(ablation["variants"]),
                "scale_records": scale["fixture"]["records"],
                "failure_cases": len(failures["results"]),
                "replay_cases": len(replay_cases),
                "digest": first,
            }
        )
    )


if __name__ == "__main__":
    main()
