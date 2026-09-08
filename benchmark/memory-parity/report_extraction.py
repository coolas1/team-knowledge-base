"""Summarize immutable extraction results and separately reviewed verdicts."""

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median


def row_key(row):
    return f"{row['case_id']}:{row['repetition']}:{row['engine']}"


def row_hash(row):
    return sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def token_counts(row):
    if row["engine"] == "upstream":
        usage = row.get("usage")
        if usage is None:
            return None
        # Upstream explicitly excludes reasoning from both output and total.
        return {
            "input": usage["input_tokens"],
            "visible_output": usage["output_tokens"],
            "reasoning": usage.get("thoughts_tokens", 0),
            "total": usage["total_tokens"] + usage.get("thoughts_tokens", 0),
        }
    usages = [call["usage"] for call in row.get("llm_calls", []) if call.get("usage")]
    if not usages:
        return None
    reasoning = sum(
        item.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        for item in usages
    )
    return {
        "input": sum(item["prompt_tokens"] for item in usages),
        "visible_output": sum(item["completion_tokens"] for item in usages) - reasoning,
        "reasoning": reasoning,
        "total": sum(item["total_tokens"] for item in usages),
    }


def summarize(manifest, rows, reviews, cases):
    expected = {
        f"{case}:{repeat}:{engine}"
        for case in manifest["cases"]
        for repeat in range(1, manifest["repetitions"] + 1)
        for engine in ("upstream", "tkb")
    }
    indexed = {}
    for row in rows:
        key = row_key(row)
        if key in indexed or key not in expected:
            raise ValueError(f"duplicate or unexpected result: {key}")
        indexed[key] = row
    reviewed = {}
    for review in reviews:
        key = review["key"]
        if key in reviewed or key not in indexed:
            raise ValueError(f"duplicate or unknown review: {key}")
        if review["row_sha256"] != row_hash(indexed[key]):
            raise ValueError(f"review evidence changed: {key}")
        if type(review.get("passed")) is not bool or not review.get("reason"):
            raise ValueError(f"review requires verdict and reason: {key}")
        reviewed[key] = review
    categories = defaultdict(lambda: defaultdict(list))
    engines = {}
    for engine in ("upstream", "tkb"):
        selected = [row for row in rows if row["engine"] == engine]
        durations = sorted(row["duration_seconds"] for row in selected)
        usages = [token_counts(row) for row in selected]
        engines[engine] = {
            "records": len(selected),
            "failed_or_degraded": sum(row["status"] != "completed" for row in selected),
            "p50_seconds": median(durations) if durations else None,
            "p95_seconds": durations[math.ceil(0.95 * len(durations)) - 1]
            if durations
            else None,
            "missing_usage_records": sum(value is None for value in usages),
            "tokens": {
                key: sum(value[key] for value in usages if value is not None)
                for key in ("input", "visible_output", "reasoning", "total")
            },
        }
        for row in selected:
            review = reviewed.get(row_key(row))
            if review:
                for category in cases[row["case_id"]]["categories"]:
                    categories[category][engine].append(
                        review["passed"] and row["status"] == "completed"
                    )
    scores = {
        category: {
            engine: sum(values) / len(values) for engine, values in grouped.items()
        }
        for category, grouped in categories.items()
    }
    complete = (
        expected == indexed.keys() == reviewed.keys()
        and manifest["status"] == "executed_pending_review"
    )
    passed = (
        complete
        and bool(scores)
        and all(
            not value["failed_or_degraded"] and not value["missing_usage_records"]
            for value in engines.values()
        )
        and all(
            values.get("tkb", 0) >= 0.9
            and values.get("tkb", 0) >= values.get("upstream", 1) - 0.05
            for values in scores.values()
        )
    )
    return {
        "scope": "extraction_only",
        "status": "passed" if passed else "failed" if complete else "incomplete",
        "expected_records": len(expected),
        "missing_results": sorted(expected - indexed.keys()),
        "missing_reviews": sorted(expected - reviewed.keys()),
        "engines": engines,
        "semantic_scores": scores,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (args.run / "results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    review_file = args.run / "reviews.json"
    reviews = (
        json.loads(review_file.read_text(encoding="utf-8"))
        if review_file.exists()
        else []
    )
    cases = {
        case["id"]: case
        for case in json.loads(
            Path(__file__).with_name("cases.json").read_text(encoding="utf-8")
        )["cases"]
    }
    report = summarize(manifest, rows, reviews, cases)
    (args.run / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
