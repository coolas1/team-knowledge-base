from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).with_name("run_ablation.py")
    spec = importlib.util.spec_from_file_location("retrieval_refine_ablation", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_ranking_ablations_emit_quality_coverage_and_latency():
    module = _module()
    report = module.run(repetitions=20)
    recorded = json.loads(
        Path(__file__).with_name("ablation_report.json").read_text(encoding="utf-8")
    )
    assert set(report["variants"]) == set(module.VARIANTS)
    assert report["selected_configuration"] == "safety_lane"
    assert recorded["selected_configuration"] == report["selected_configuration"]
    assert {
        name: {key: value for key, value in result.items() if key != "latency_ms"}
        for name, result in recorded["variants"].items()
    } == {
        name: {key: value for key, value in result.items() if key != "latency_ms"}
        for name, result in report["variants"].items()
    }
    for result in report["variants"].values():
        assert {
            "mrr",
            "ndcg_at_5",
            "recall_at_5",
            "unique_document_coverage",
            "latency_ms",
        } <= result.keys()
        assert result["latency_ms"]["p50"] >= 0
        assert result["latency_ms"]["p95"] >= result["latency_ms"]["p50"]
    assert (
        report["variants"]["source_isolation"]["recall_at_5"]
        >= report["variants"]["current"]["recall_at_5"]
    )
    assert (
        report["variants"]["safety_lane"]["recall_at_5"]
        > report["variants"]["hierarchical"]["recall_at_5"]
    )
