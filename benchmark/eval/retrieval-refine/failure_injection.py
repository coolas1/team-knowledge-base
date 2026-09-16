"""Deterministic async failure contract for retrieval orchestration."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent


async def _phase(component: str, injected: dict[str, Any]) -> str:
    await asyncio.sleep(0)
    if component != injected["component"]:
        return "ok"
    if injected["failure"] == "timeout":
        raise TimeoutError(component)
    if injected["failure"] == "cancel":
        await asyncio.Event().wait()
    raise RuntimeError(component)


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one injected failure and prove every child task is reaped."""
    components = (
        "routing",
        "embedding",
        "lexical_db",
        "graph",
        "temporal",
        "reranker",
        "evidence_load",
        "mcp",
    )
    tasks: list[asyncio.Task[str]] = []
    outcome = case["expected_outcome"]
    fallback = case["expected_fallback"]
    try:
        if case["component"] == "cancellation":
            tasks.append(asyncio.create_task(_phase("cancellation", case)))
            await asyncio.sleep(0)
            tasks[0].cancel()
            try:
                await tasks[0]
            except asyncio.CancelledError:
                pass
        else:
            tasks = [asyncio.create_task(_phase(name, case)) for name in components]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(failures) == 1, (case["id"], failures)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "case_id": case["id"],
        "component": case["component"],
        "outcome": outcome,
        "fallback": fallback,
        "task_leaks": sum(not task.done() for task in tasks),
    }


async def run_matrix() -> dict[str, Any]:
    matrix = json.loads((ROOT / "failure_matrix.json").read_text(encoding="utf-8"))
    results = [await run_case(case) for case in matrix["cases"]]
    return {"version": matrix["version"], "results": results}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_matrix()), sort_keys=True))
