"""Deterministic B4 latency/usage comparison over the production RecallEngine."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.hindsight_components.config import HindsightOptions  # noqa: E402
from src.engine.hindsight_components.recall import RecallEngine  # noqa: E402
from src.engine.hindsight_components.types import (  # noqa: E402
    RecallCandidate,
    RecallFilter,
)


class Providers:
    async def embed(self, texts, **_kwargs):
        return [[1.0, 0.0] for _ in texts]

    async def json(self, *_args, **_kwargs):
        return {"ranking": []}


class Repository:
    def __init__(self):
        self.rows = [
            RecallCandidate(
                id=f"memory-{index}",
                document_id=f"document-{index}",
                title=f"source-{index}",
                text=f"durable project fact {index}",
                source_text=f"source text {index}",
                chunk_index=index,
                memory_type="observation" if index % 2 else "world",
                source_type="conversation",
                embedding=[1.0, 0.0],
                semantic_score=0.9 - index / 100,
                keyword_score=0.8 - index / 100,
            )
            for index in range(20)
        ]

    async def semantic_search(self, _embedding, limit, **_kwargs):
        return self.rows[:limit]

    async def keyword_search(self, _query, limit, **_kwargs):
        return list(reversed(self.rows[:limit]))

    async def entity_states(self, _ids):
        return {}

    async def recall_details(self, ids, *, include_source_facts=False):
        return {
            identity: {
                "freshness": "active",
                "stale_reason": None,
                "updated_at": "2026-09-09T00:00:00+00:00",
                "source_facts": [],
            }
            for identity in ids
        }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


async def measure(engine, repetitions, filters=None):
    durations = []
    tokens = []
    for _ in range(repetitions):
        started = time.perf_counter()
        result = await engine.recall(
            "project fact", mode="fast", top_k=10, filters=filters
        )
        durations.append((time.perf_counter() - started) * 1000)
        tokens.append(result.trace["token_count"])
    return {
        "calls": repetitions,
        "p50_ms": round(statistics.median(durations), 3),
        "p95_ms": round(percentile(durations, 0.95), 3),
        "mean_output_tokens": round(statistics.mean(tokens), 3),
        "model_tokens": 0,
    }


async def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    engine = RecallEngine(Repository(), Providers(), HindsightOptions())
    result = {
        "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tkb_sha": subprocess.check_output(["git", "rev-parse", "HEAD"])
        .decode()
        .strip(),
        "repetitions": args.repetitions,
        "legacy_default": await measure(engine, args.repetitions),
        "b4_filtered": await measure(
            engine,
            args.repetitions,
            RecallFilter(
                memory_types=("observation",),
                source_types=("conversation",),
                prefer_observations=True,
                include=("source_facts",),
                max_tokens=512,
                max_candidates=30,
            ),
        ),
        "notes": [
            "Production RecallEngine with deterministic in-memory ports; no network or model variability.",
            "This is a contract overhead comparison, not an upstream relevance benchmark.",
        ],
    }
    (output / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=25)
    arguments = parser.parse_args()
    if not 1 <= arguments.repetitions <= 1000:
        parser.error("repetitions must be 1..1000")
    asyncio.run(main(arguments))
