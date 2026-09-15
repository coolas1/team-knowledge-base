from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parent


def _module():
    path = ROOT / "failure_injection.py"
    spec = importlib.util.spec_from_file_location("retrieval_failure_injection", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_failure_matrix_reports_truthful_fallbacks_without_task_leaks():
    matrix = json.loads((ROOT / "failure_matrix.json").read_text(encoding="utf-8"))
    before = {task for task in asyncio.all_tasks() if not task.done()}
    report = await _module().run_matrix()
    await asyncio.sleep(0)
    after = {task for task in asyncio.all_tasks() if not task.done()}

    assert [result["case_id"] for result in report["results"]] == [
        case["id"] for case in matrix["cases"]
    ]
    assert {
        "empty",
        "degraded",
        "timeout",
        "unavailable",
        "fallback",
    } <= {result["outcome"] for result in report["results"]}
    assert all(result["task_leaks"] == 0 for result in report["results"])
    assert after <= before

    expected = {
        case["id"]: (case["expected_outcome"], case["expected_fallback"])
        for case in matrix["cases"]
    }
    assert {
        result["case_id"]: (result["outcome"], result["fallback"])
        for result in report["results"]
    } == expected
