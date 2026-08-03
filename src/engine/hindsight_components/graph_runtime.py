"""Lifecycle-managed background worker for the Hindsight graph projection."""

from __future__ import annotations

import asyncio
import logging

from .graph_outbox import GraphProjectionWorker, PostgresGraphOutbox
from .graph_projector import MemoryGraphProjector
from .neo4j_graph import HindsightNeo4jGraphStore
from .repository import PostgresMemoryRepository

logger = logging.getLogger(__name__)


class GraphWorkerRuntime:
    def __init__(
        self,
        worker: GraphProjectionWorker,
        projector: MemoryGraphProjector,
        store: HindsightNeo4jGraphStore,
        *,
        poll_seconds: float = 1.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._worker = worker
        self._projector = projector
        self._store = store
        self._poll_seconds = poll_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            await self._projector.ensure_schema()
        except Exception:
            await self._store.close()
            raise
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run(),
            name="hindsight-graph-worker",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        try:
            await task
        finally:
            self._task = None
            await self._store.close()

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = await self._worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hindsight graph worker iteration failed")
                result = None
            if result is None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_seconds,
                    )
                except TimeoutError:
                    pass


def build_graph_worker_runtime(
    *,
    poll_seconds: float = 1.0,
    lease_seconds: int = 300,
    max_attempts: int = 10,
) -> GraphWorkerRuntime:
    store = HindsightNeo4jGraphStore()
    projector = MemoryGraphProjector(store)
    worker = GraphProjectionWorker(
        PostgresGraphOutbox(),
        PostgresMemoryRepository(),
        projector,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )
    return GraphWorkerRuntime(
        worker,
        projector,
        store,
        poll_seconds=poll_seconds,
    )
