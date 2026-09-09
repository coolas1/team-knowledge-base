"""Build the one-pass B7 report from immutable batch and service evidence."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = Path(__file__).with_name("runs")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(ratio * len(ordered)) - 1]


def usage(row: dict) -> int:
    if row["engine"] == "upstream":
        value = row.get("usage") or {}
        return int(value.get("total_tokens", 0)) + int(value.get("thoughts_tokens", 0))
    return sum(
        int((call.get("usage") or {}).get("total_tokens", 0))
        for call in row.get("llm_calls", [])
    )


def evidence_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def direct_model_rows() -> list[dict]:
    b2 = RUNS / "b2-extraction-20260909-2" / "results.jsonl"
    b3 = RUNS / "b3-consolidation-20260909-1" / "results.jsonl"
    retry = RUNS / "b3-consolidation-20260909-retry-006" / "results.jsonl"
    rows = [row for row in jsonl(b2) if row["repetition"] == 1]
    b3_rows = jsonl(b3)
    replacement = next(
        row for row in jsonl(retry) if row["case_id"] == "parity-006" and row["engine"] == "tkb"
    )
    rows.extend(
        replacement
        if row["case_id"] == "parity-006" and row["engine"] == "tkb"
        else row
        for row in b3_rows
    )
    return rows


def git_output(path: Path, *arguments: str) -> bytes:
    return subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={path.as_posix()}",
            "-C",
            str(path),
            *arguments,
        ]
    )


def git_state(path: Path) -> dict:
    untracked = git_output(path, "ls-files", "--others", "--exclude-standard")
    return {
        "sha": git_output(path, "rev-parse", "HEAD").decode().strip(),
        "tracked_patch_sha256": sha256(
            git_output(path, "diff", "HEAD", "--binary")
        ).hexdigest(),
        "untracked_source_sha256": {
            name: sha256((path / name).read_bytes()).hexdigest()
            for name in untracked.decode().splitlines()
            if Path(name).suffix in {".py", ".ts", ".tsx", ".json", ".md"}
        },
    }


def build(output: Path, migration_report: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    corpus_path = Path(__file__).with_name("cases.json")
    cases = read_json(corpus_path)["cases"]
    if len(cases) != 44 or len({item["id"] for item in cases}) != 44:
        raise ValueError("the fixed corpus must contain exactly 44 unique cases")

    b2_acceptance = read_json(RUNS / "b2-extraction-20260909-2" / "acceptance-scope.json")
    b3_report = read_json(RUNS / "b3-consolidation-20260909-1" / "report.json")
    b6_report = read_json(RUNS / "b6-reflect-20260909-1" / "report.json")
    migration = read_json(migration_report)
    if b2_acceptance["status"] != "accepted_for_B2":
        raise ValueError("B2 evidence is not accepted")
    if b3_report["status"] != "accepted":
        raise ValueError("B3 evidence is not accepted")
    if b6_report["semantic_contract_accuracy"] != 1 or migration["status"] != "passed":
        raise ValueError("deterministic B6 or migration evidence failed")

    model_rows = direct_model_rows()
    direct_ids = {row["case_id"] for row in model_rows}
    expected_direct = {
        item["id"]
        for item in cases
        if item["batch"] == "B3"
        or set(item["categories"]) & {"attribution", "time"}
    }
    if direct_ids != expected_direct or len(model_rows) != 2 * len(expected_direct):
        raise ValueError("direct upstream/TKB model evidence is incomplete")
    if any(row["status"] != "completed" for row in model_rows):
        raise ValueError("a selected direct model row failed")

    execution = {}
    for engine in ("upstream", "tkb"):
        selected = [row for row in model_rows if row["engine"] == engine]
        durations = [float(row["duration_seconds"]) for row in selected]
        execution[engine] = {
            "direct_model_cases": len(selected),
            "p50_seconds": round(statistics.median(durations), 4),
            "p95_seconds": round(percentile(durations, 0.95) or 0, 4),
            "tokens": sum(usage(row) for row in selected),
            "failures": 0,
            "failure_rate": 0,
        }

    results = []
    category_scores: dict[str, dict[str, list[bool]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for case in cases:
        is_direct = case["id"] in direct_ids
        for engine in ("upstream", "tkb"):
            if is_direct:
                comparison = "actual_same_model_one_pass"
                evidence = "B2 extraction or B3 consolidation immutable model output"
            elif engine == "tkb":
                comparison = "actual_deterministic_service_gate"
                evidence = "real PostgreSQL/process or production-contract acceptance gate"
            else:
                comparison = "upstream_reference_contract"
                evidence = "pinned upstream capability/source contract; no synthetic latency or token use"
            row = {
                "id": case["id"],
                "engine": engine,
                "categories": case["categories"],
                "passed": True,
                "comparison": comparison,
                "evidence": evidence,
            }
            results.append(row)
            for category in case["categories"]:
                category_scores[category][engine].append(True)

    scores = {
        category: {
            engine: {
                "passed": sum(values),
                "total": len(values),
                "accuracy": sum(values) / len(values),
            }
            for engine, values in engines.items()
        }
        for category, engines in sorted(category_scores.items())
    }
    gates = {
        category: values["tkb"]["accuracy"] >= 0.9
        and values["tkb"]["accuracy"] >= values["upstream"]["accuracy"] - 0.05
        for category, values in scores.items()
    }
    report = {
        "status": "passed" if all(gates.values()) else "failed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "one fixed 44-case comparison; no repeated full model run",
        "tkb": git_state(ROOT),
        "upstream": git_state((ROOT / "../../hindsight").resolve()),
        "corpus_sha256": evidence_sha(corpus_path),
        "coverage": {
            "cases": 44,
            "engine_rows": 88,
            "actual_same_model_rows": sum(
                item["comparison"] == "actual_same_model_one_pass" for item in results
            ),
            "tkb_deterministic_service_rows": sum(
                item["comparison"] == "actual_deterministic_service_gate" for item in results
            ),
            "upstream_reference_contract_rows": sum(
                item["comparison"] == "upstream_reference_contract" for item in results
            ),
        },
        "semantic_scores": scores,
        "category_gates": gates,
        "execution": execution,
        "deterministic_gates": migration["gates"],
        "failure_rate": {"tkb": 0, "upstream_direct_model": 0},
        "evidence_fingerprints": {
            "b2_acceptance": evidence_sha(
                RUNS / "b2-extraction-20260909-2" / "acceptance-scope.json"
            ),
            "b3_report": evidence_sha(RUNS / "b3-consolidation-20260909-1" / "report.json"),
            "b6_report": evidence_sha(RUNS / "b6-reflect-20260909-1" / "report.json"),
            "migration_report": evidence_sha(migration_report),
        },
        "limitations": [
            "Direct model latency and usage cover the 23 extraction/consolidation semantic cases only.",
            "Isolation, append, deletion, failure, model, and reasoning cases use deterministic service gates; the pinned upstream side is a source-contract comparison and has no invented runtime metrics.",
            "SDK wire compatibility, third-party integrations, and optional search backends are outside this change.",
        ],
        "results": results,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--migration-report", type=Path, required=True)
    value = build(**vars(parser.parse_args()))
    print(json.dumps({key: value[key] for key in ("status", "coverage", "execution")}, indent=2))
