from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).with_name("run_scale.py")
    spec = importlib.util.spec_from_file_location("retrieval_refine_scale_run", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scale_report_bounds_candidates_memory_latency_outcomes_and_payload():
    report = _module().run(records=30_000, queries=100)
    assert report["fixture"]["records"] >= 30_000
    assert report["candidate_max"] <= report["candidate_bound"] == 300
    assert (
        report["database_plan"]["required_index"] == "idx_memory_units_lexical_tokens"
    )
    assert 0 < report["peak_memory_mb"] < 256
    for phase in report["phase_latency_ms"].values():
        assert 0 <= phase["p50"] <= phase["p95"] <= phase["p99"]
    assert all(report["deep_outcomes"].values())
    assert (
        report["response_budget"]["max_bytes"]
        <= report["response_budget"]["limit_bytes"]
    )
