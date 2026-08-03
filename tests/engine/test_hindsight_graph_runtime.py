from __future__ import annotations

import asyncio

import pytest

from src.engine.hindsight_components.graph_runtime import GraphWorkerRuntime


class Worker:
    def __init__(self, values=None, *, error: Exception | None = None):
        self.values = list(values or [])
        self.error = error
        self.calls = 0

    async def run_once(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.values.pop(0) if self.values else None


class Projector:
    def __init__(self, *, error: Exception | None = None):
        self.schema_calls = 0
        self.error = error

    async def ensure_schema(self):
        self.schema_calls += 1
        if self.error is not None:
            raise self.error


class Store:
    def __init__(self):
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1


async def test_runtime_ensures_schema_runs_worker_and_closes_store():
    worker = Worker()
    projector = Projector()
    store = Store()
    runtime = GraphWorkerRuntime(worker, projector, store, poll_seconds=0.01)

    await runtime.start()
    await asyncio.sleep(0)
    await runtime.stop()

    assert projector.schema_calls == 1
    assert worker.calls >= 1
    assert store.close_calls == 1


async def test_runtime_start_and_stop_are_idempotent():
    projector = Projector()
    store = Store()
    runtime = GraphWorkerRuntime(Worker(), projector, store, poll_seconds=0.01)

    await runtime.start()
    await runtime.start()
    await runtime.stop()
    await runtime.stop()

    assert projector.schema_calls == 1
    assert store.close_calls == 1


def test_runtime_rejects_non_positive_poll_interval():
    with pytest.raises(ValueError):
        GraphWorkerRuntime(Worker(), Projector(), Store(), poll_seconds=0)


async def test_runtime_closes_store_when_schema_setup_fails():
    store = Store()
    runtime = GraphWorkerRuntime(
        Worker(),
        Projector(error=RuntimeError("neo4j unavailable")),
        store,
    )

    with pytest.raises(RuntimeError, match="neo4j unavailable"):
        await runtime.start()

    assert store.close_calls == 1
