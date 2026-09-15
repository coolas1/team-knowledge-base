from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent


def _metrics():
    spec = importlib.util.spec_from_file_location(
        "retrieval_refine_honesty_metrics", ROOT / "metrics.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_answer_and_weak_overlap_respect_versioned_honesty_tolerances():
    payload = json.loads((ROOT / "honesty_cases.json").read_text(encoding="utf-8"))
    cases = payload["cases"]
    results = {case["id"]: case["fixture_result"] for case in cases}
    rates = _metrics().honesty_rates(cases, results)

    assert {case["kind"] for case in cases} >= {
        "no_answer",
        "weak_overlap",
        "metadata_only",
    }
    assert all(
        rates[name] <= tolerance for name, tolerance in payload["tolerances"].items()
    )
    assert all(
        set(result["citation_ids"]) <= set(result["document_ids"])
        for result in results.values()
    )
