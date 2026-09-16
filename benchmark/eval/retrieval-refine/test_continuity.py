from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent


def _metrics():
    spec = importlib.util.spec_from_file_location(
        "retrieval_refine_metrics", ROOT / "metrics.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_continuity_fixture_covers_memory_authority_and_meets_gates():
    payload = json.loads((ROOT / "continuity_cases.json").read_text(encoding="utf-8"))
    cases = payload["cases"]
    results = {case["id"]: case["fixture_result"] for case in cases}
    candidate_states = {
        (candidate["freshness"], candidate["authority"])
        for case in cases
        for candidate in case["candidates"]
    }

    assert {case["expected_route"] for case in cases} == {
        "knowledge",
        "conversation",
        "mixed",
    }
    assert ("active", "user_confirmed") in candidate_states
    assert ("active", "unconfirmed") in candidate_states
    assert any(state in {"stale", "superseded"} for state, _ in candidate_states)
    assert (
        _metrics().route_accuracy(cases, results)
        >= payload["thresholds"]["route_accuracy"]
    )
    assert (
        _metrics().conversation_precision_at_k(cases, results, k=3)
        >= payload["thresholds"]["conversation_precision_at_3"]
    )
