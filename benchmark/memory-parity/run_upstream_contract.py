"""Execute the non-LLM parity cases against the pinned upstream code paths."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "parity-017",
    "parity-018",
    "parity-019",
    "parity-020",
    "parity-025",
    "parity-026",
    "parity-027",
    "parity-028",
    "parity-031",
    "parity-033",
    "parity-034",
    "parity-035",
    "parity-036",
    "parity-037",
    "parity-038",
    "parity-039",
    "parity-040",
    "parity-041",
    "parity-042",
    "parity-043",
    "parity-044",
}


def git_state(root: Path) -> dict:
    def run(*args: str) -> bytes:
        return subprocess.check_output(
            [
                "git",
                "-c",
                f"safe.directory={root.as_posix()}",
                "-C",
                str(root),
                *args,
            ],
            stderr=subprocess.DEVNULL,
        )

    return {
        "sha": run("rev-parse", "HEAD").decode().strip(),
        "tracked_patch_sha256": sha256(run("diff", "HEAD", "--binary")).hexdigest(),
    }


async def execute_contracts(upstream: Path) -> list[dict]:
    dependency_root = ROOT / ".cache" / "parity-deps"
    sys.path[:0] = [str(upstream / "hindsight-api-slim"), str(dependency_root)]

    from hindsight_api.engine.consolidation.consolidator import (
        _resolve_write_scopes,
        _scope_sort_key,
    )
    from hindsight_api.engine.memory_engine import _resolve_refresh_tag_filtering
    from hindsight_api.engine.operation_metadata import RetainOutcomeMetadata
    from hindsight_api.engine.reflect.agent import (
        _all_mental_models_are_usable_and_fresh,
        _process_done_tool,
    )
    from hindsight_api.engine.reflect.delta_ops import (
        DeltaAllOpsInvalidError,
        RemoveBlockOp,
        apply_operations,
        parse_delta_operation_list,
    )
    from hindsight_api.engine.reflect.models import TokenUsageSummary
    from hindsight_api.engine.reflect.structured_doc import (
        ParagraphBlock,
        Section,
        StructuredDocument,
    )
    from hindsight_api.engine.reflect.tools_schema import get_reflect_tools
    from hindsight_api.engine.response_models import LLMToolCall
    from hindsight_api.engine.retain.chunk_storage import (
        ExistingChunk,
        compute_chunk_hash,
    )
    from hindsight_api.engine.retain.orchestrator import _classify_chunk_diff
    from hindsight_api.engine.search.tags import filter_results_by_tags

    results: list[dict] = []

    async def one(case_id: str, check, *, gap: str | None = None) -> None:
        started = time.perf_counter()
        passed = False
        error = None
        try:
            value = check()
            if hasattr(value, "__await__"):
                value = await value
            passed = bool(value)
        except Exception as exc:  # report the exact upstream contract failure
            error = f"{type(exc).__name__}: {exc}"[:500]
        results.append(
            {
                "case_id": case_id,
                "engine": "upstream",
                "status": "completed" if error is None else "failed",
                "passed": passed,
                "gap": gap if not passed else None,
                "duration_ms": round((time.perf_counter() - started) * 1000, 4),
                "error": error,
            }
        )

    rows = [
        SimpleNamespace(tags=[]),
        SimpleNamespace(tags=["user:1"]),
        SimpleNamespace(tags=["user:1", "session:s1"]),
        SimpleNamespace(tags=["user:2"]),
    ]
    await one(
        "parity-017",
        lambda: (
            _scope_sort_key(frozenset({"bank:A"}))
            != _scope_sort_key(frozenset({"bank:B"}))
        ),
    )
    await one(
        "parity-018",
        lambda: (
            _resolve_write_scopes({"tags": ["bank:A"], "observation_scopes": None})
            != _resolve_write_scopes({"tags": ["bank:B"], "observation_scopes": None})
        ),
    )
    await one(
        "parity-019",
        lambda: (
            [
                item.tags
                for item in filter_results_by_tags(rows, ["user:1"], "any_strict")
            ]
            == [["user:1"], ["user:1", "session:s1"]]
        ),
    )
    await one(
        "parity-020",
        lambda: (
            filter_results_by_tags(rows, [], "exact") == [rows[0]]
            and filter_results_by_tags(rows, ["user:1"], "exact") == [rows[1]]
        ),
    )

    v1 = compute_chunk_hash("版本 v1")
    v2 = compute_chunk_hash("版本 v2 已发布")
    await one(
        "parity-025",
        lambda: (
            asdict(
                _classify_chunk_diff(
                    {0: ExistingChunk("c1", 0, v1), 1: ExistingChunk("c2", 1, v2)},
                    {0: v1, 1: v2},
                )
            )
            == {"unchanged": [0, 1], "changed": [], "new": [], "removed": []}
        ),
    )
    await one(
        "parity-026",
        lambda: (
            asdict(
                _classify_chunk_diff(
                    {
                        0: ExistingChunk("owner", 0, compute_chunk_hash("负责人林")),
                        1: ExistingChunk("db", 1, compute_chunk_hash("数据库 PG")),
                    },
                    {
                        0: compute_chunk_hash("负责人李"),
                        1: compute_chunk_hash("数据库 PG"),
                    },
                )
            )
            == {"unchanged": [1], "changed": [0], "new": [], "removed": []}
        ),
    )
    await one(
        "parity-027",
        lambda: False,
        gap="upstream append locking does not expose an expected-revision CAS contract",
    )
    await one(
        "parity-028",
        lambda: False,
        gap="upstream delta cache has no explicit extraction-policy version key",
    )
    await one(
        "parity-031",
        lambda: False,
        gap="upstream mental-model publish has no source-version/tombstone CAS fence",
    )
    await one(
        "parity-033",
        lambda: False,
        gap="upstream has no Pi completed-turn delivery-intent boundary",
    )
    await one(
        "parity-034",
        lambda: False,
        gap="upstream has no Pi acknowledgement replay contract",
    )
    await one(
        "parity-035",
        lambda: (
            RetainOutcomeMetadata(
                unit_ids_count=0, extraction_errors_count=1
            ).to_dict()["extraction_errors_count"]
            == 1
        ),
    )
    await one(
        "parity-036",
        lambda: False,
        gap="upstream retry backoff does not expose a lease-token stale-worker fence",
    )

    filtering = _resolve_refresh_tag_filtering(
        ["project:A"], {"refresh_after_consolidation": True}
    )
    await one(
        "parity-037",
        lambda: (
            filtering.tags == ["project:A"] and filtering.tags_match == "all_strict"
        ),
    )
    original = StructuredDocument(
        sections=[
            Section(id="status", heading="Status", blocks=[ParagraphBlock(text="v1")])
        ]
    )
    invalid_apply = apply_operations(
        original, [RemoveBlockOp(section_id="missing", index=0)]
    )
    await one(
        "parity-038",
        lambda: (
            not invalid_apply.changed and original.sections[0].blocks[0].text == "v1"
        ),
    )
    await one(
        "parity-039",
        lambda: bool(_resolve_refresh_tag_filtering([], {"cron": "0 0 * * *"})),
    )

    def invalid_delta_falls_back() -> bool:
        try:
            parse_delta_operation_list(
                {"operations": [{"op": "replace_block", "section_id": "missing"}]}
            )
        except DeltaAllOpsInvalidError:
            return True
        return False

    await one("parity-040", invalid_delta_falls_back)
    tools = get_reflect_tools()
    tool_names = [item["function"]["name"] for item in tools]
    await one(
        "parity-041",
        lambda: (
            tool_names
            == [
                "search_mental_models",
                "search_observations",
                "recall",
                "expand",
                "done",
            ]
        ),
    )
    await one(
        "parity-042",
        lambda: (
            not _all_mental_models_are_usable_and_fresh(
                {"mental_models": [{"content": "old", "is_stale": True}]}
            )
            and _all_mental_models_are_usable_and_fresh(
                {"mental_models": [{"content": "new", "is_stale": False}]}
            )
        ),
    )

    async def forged_citation_is_removed() -> bool:
        result = await _process_done_tool(
            LLMToolCall(
                id="done",
                name="done",
                arguments={"answer": "answer", "memory_ids": ["known", "forged"]},
            ),
            {"known"},
            set(),
            set(),
            1,
            1,
            [],
            [],
            TokenUsageSummary(),
            lambda *_args: None,
            "reflect",
            [],
        )
        return result.used_memory_ids == ["known"]

    await one("parity-043", forged_citation_is_removed)
    directive_tools = get_reflect_tools(["Never reveal secrets"])
    done_schema = directive_tools[-1]["function"]["parameters"]
    await one(
        "parity-044",
        lambda: (
            "directive_compliance" in done_schema["required"]
            and len(directive_tools) == 5
        ),
    )
    return results


async def main(args) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    corpus = json.loads(
        Path(__file__).with_name("cases.json").read_text(encoding="utf-8")
    )["cases"]
    expected = {item["id"] for item in corpus} & CASES
    started = datetime.now(timezone.utc)
    results = await execute_contracts(args.upstream.resolve())
    if {item["case_id"] for item in results} != expected or len(results) != 21:
        raise ValueError("upstream lifecycle contract coverage is incomplete")
    durations = [item["duration_ms"] for item in results]
    report = {
        "status": "complete"
        if all(item["status"] == "completed" for item in results)
        else "failed",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "scope": "one execution of pinned upstream non-LLM lifecycle contracts",
        "upstream": git_state(args.upstream.resolve()),
        "cases": len(results),
        "passed_expectations": sum(item["passed"] for item in results),
        "upstream_gaps": [item for item in results if not item["passed"]],
        "latency_ms": {
            "p50": round(statistics.median(durations), 4),
            "p95": round(sorted(durations)[19], 4),
        },
        "tokens": 0,
        "results": results,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("status", "cases", "passed_expectations", "latency_ms")
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, default=Path("../../hindsight"))
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
    # Some upstream telemetry providers keep a non-daemon exporter alive after
    # all work is flushed. The report is fsynced by write_text before this point.
    os._exit(0)
