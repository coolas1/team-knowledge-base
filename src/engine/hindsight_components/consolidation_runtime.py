"""Lifecycle wrapper for the persistent consolidation worker."""

from __future__ import annotations

import asyncio
import logging

from .consolidation import (
    ConsolidationOptions,
    ConsolidationWorker,
    PostgresConsolidationRepository,
)
from .providers import ProjectHindsightProviders

logger = logging.getLogger(__name__)


class ConsolidationWorkerRuntime:
    def __init__(self, worker, *, poll_seconds: float = 1.0, max_concurrent: int = 1):
        if poll_seconds <= 0 or max_concurrent < 1:
            raise ValueError("consolidation runtime bounds must be positive")
        self.worker = worker
        self.poll_seconds = poll_seconds
        self.max_concurrent = max_concurrent
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        if self._tasks:
            return
        self._stop.clear()
        self._tasks = [
            asyncio.create_task(self._run(), name=f"memory-consolidation-{index}")
            for index in range(self.max_concurrent)
        ]

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._stop.set()
        try:
            await asyncio.gather(*self._tasks)
        finally:
            self._tasks = []

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = await self.worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("memory consolidation iteration failed")
                result = None
            if result is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass


def build_consolidation_worker_runtime(
    *,
    poll_seconds: float = 1.0,
    max_concurrent: int = 1,
    batch_size: int = 64,
    observation_limit: int = 1000,
    max_iterations: int = 8,
    max_tokens: int = 32000,
    max_cost_usd: float = 0,
    input_cost_usd_per_million: float = 0,
    output_cost_usd_per_million: float = 0,
    semantic_dedup_enabled: bool = True,
    semantic_threshold: float = 0.9,
) -> ConsolidationWorkerRuntime:
    options = ConsolidationOptions(
        batch_size=batch_size,
        observation_limit=observation_limit,
        max_iterations=max_iterations,
        max_tokens=max_tokens,
        max_cost_microusd=round(max_cost_usd * 1_000_000),
        input_cost_usd_per_million=input_cost_usd_per_million,
        output_cost_usd_per_million=output_cost_usd_per_million,
        semantic_dedup_enabled=semantic_dedup_enabled,
        semantic_threshold=semantic_threshold,
    )
    return ConsolidationWorkerRuntime(
        ConsolidationWorker(
            PostgresConsolidationRepository(), ProjectHindsightProviders(), options
        ),
        poll_seconds=poll_seconds,
        max_concurrent=max_concurrent,
    )
