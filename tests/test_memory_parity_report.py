import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "parity_report",
    Path(__file__).parents[1] / "benchmark/memory-parity/report_extraction.py",
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

consolidation_spec = importlib.util.spec_from_file_location(
    "parity_consolidation",
    Path(__file__).parents[1] / "benchmark/memory-parity/run_consolidation.py",
)
consolidation = importlib.util.module_from_spec(consolidation_spec)
consolidation_spec.loader.exec_module(consolidation)


def test_reasoning_is_counted_once_for_both_provider_conventions():
    upstream = {
        "engine": "upstream",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "thoughts_tokens": 8,
        },
    }
    tkb = {
        "engine": "tkb",
        "llm_calls": [
            {
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 8},
                }
            }
        ],
    }
    assert (
        report.token_counts(upstream)
        == report.token_counts(tkb)
        == {"input": 10, "visible_output": 2, "reasoning": 8, "total": 20}
    )


def test_missing_or_changed_evidence_cannot_pass_acceptance():
    manifest = {
        "cases": ["case"],
        "repetitions": 1,
        "status": "executed_pending_review",
    }
    row = {
        "case_id": "case",
        "repetition": 1,
        "engine": "tkb",
        "status": "completed",
        "duration_seconds": 1,
    }
    review = {
        "key": report.row_key(row),
        "row_sha256": report.row_hash(row),
        "passed": True,
        "reason": "Manually checked fact attribution.",
    }
    cases = {"case": {"categories": ["attribution"]}}
    assert report.summarize(manifest, [row], [review], cases)["status"] == "incomplete"
    with pytest.raises(ValueError, match="duplicate"):
        report.summarize(manifest, [row, row], [review], cases)
    row["status"] = "degraded"
    with pytest.raises(ValueError, match="evidence changed"):
        report.summarize(manifest, [row], [review], cases)


async def test_consolidation_observer_forwards_completion_limit(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        async def aread(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "{}"}}]}

        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, json):
            captured.update(json)
            assert headers["Authorization"] == "Bearer secret"
            return Response()

    monkeypatch.setattr(consolidation.httpx, "AsyncClient", lambda **_kwargs: Client())
    monkeypatch.setattr(
        consolidation,
        "settings",
        SimpleNamespace(
            llm=SimpleNamespace(
                require_model=lambda: "test-model",
                base_url="https://example.test/v1",
                api_key="secret",
            )
        ),
    )

    value = await consolidation.ObservedProvider()._complete(
        "system", "user", json_mode=True, timeout=1, max_tokens=321
    )

    assert value == "{}"
    assert captured["max_tokens"] == 321
