"""Lifecycle wrapper for scheduled and event-driven mental-model refresh."""

from __future__ import annotations

import asyncio
import logging

from .config import HindsightOptions
from .mental_models import (
    MentalModelRefreshOptions,
    MentalModelRefreshWorker,
    PostgresMentalModelRepository,
)
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService

logger = logging.getLogger(__name__)


class MentalModelWorkerRuntime:
    def __init__(
        self, repository, worker, *, poll_seconds: float = 5, max_concurrent: int = 1
    ):
        if poll_seconds <= 0 or max_concurrent < 1:
            raise ValueError("mental model runtime bounds must be positive")
        self.repository = repository
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
            asyncio.create_task(self._run(), name=f"mental-model-refresh-{index}")
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
                await self.repository.schedule_due()
                result = await self.worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mental model refresh iteration failed")
                result = None
            if result is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass


def build_mental_model_worker_runtime(
    *,
    poll_seconds: float = 5,
    max_concurrent: int = 1,
    recall_results: int = 30,
    max_evidence_tokens: int = 4096,
    max_output_tokens: int = 2048,
    lease_seconds: int = 300,
    max_attempts: int = 5,
    input_cost_usd_per_million: float = 0,
    output_cost_usd_per_million: float = 0,
    use_adaptive_reflect: bool = False,
) -> MentalModelWorkerRuntime:
    repository = PostgresMentalModelRepository()
    providers = ProjectHindsightProviders()
    recall_options = HindsightOptions(
        adaptive_reflect_enabled=use_adaptive_reflect,
        recall_max_results=max(recall_results, 1),
        recall_max_candidates=max(recall_results * 4, recall_results),
        recall_max_tokens=max_evidence_tokens,
    )

    def recall_factory(scope):
        return HindsightService(
            PostgresMemoryRepository(scope=scope), providers, recall_options
        )

    worker = MentalModelRefreshWorker(
        repository,
        recall_factory,
        providers,
        MentalModelRefreshOptions(
            recall_results=recall_results,
            max_evidence_tokens=max_evidence_tokens,
            max_output_tokens=max_output_tokens,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            input_cost_usd_per_million=input_cost_usd_per_million,
            output_cost_usd_per_million=output_cost_usd_per_million,
            use_adaptive_reflect=use_adaptive_reflect,
        ),
        reflect_factory=recall_factory,
    )
    return MentalModelWorkerRuntime(
        repository, worker, poll_seconds=poll_seconds, max_concurrent=max_concurrent
    )
