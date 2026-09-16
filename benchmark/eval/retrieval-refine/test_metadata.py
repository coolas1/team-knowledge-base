from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parent


def _metrics():
    spec = importlib.util.spec_from_file_location(
        "retrieval_refine_metadata_metrics", ROOT / "metrics.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_fixture_covers_fields_languages_and_separate_quality_gates():
    payload = json.loads((ROOT / "metadata_cases.json").read_text(encoding="utf-8"))
    cases = payload["cases"]
    results = {case["id"]: case["fixture_result"] for case in cases}
    fields = {case["match_field"] for case in cases}
    languages = {case["language"] for case in cases}
    metrics = _metrics()

    assert {
        "title",
        "filename",
        "overview",
        "tags",
        "entities",
        "implicit_topic",
    } <= fields
    assert {"zh", "en", "ja", "multilingual"} <= languages
    assert any(case["document"]["body_quality"] == "noisy_ocr" for case in cases)
    assert (
        metrics.document_recall_at_k(cases, results, k=5)
        >= payload["thresholds"]["document_recall_at_5"]
    )
    assert (
        metrics.passage_usefulness(cases, results)
        >= payload["thresholds"]["passage_usefulness"]
    )
    assert (
        metrics.metadata_disclosure_accuracy(cases, results)
        >= payload["thresholds"]["metadata_disclosure_accuracy"]
    )
