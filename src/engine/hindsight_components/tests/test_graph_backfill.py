from __future__ import annotations

import uuid

from src.engine.hindsight_components.graph_backfill import PostgresGraphBackfill


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSession:
    def __init__(self, scalar_results):
        self.scalar_results = list(scalar_results)
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return AsyncContext()

    async def scalars(self, statement):
        return self.scalar_results.pop(0)

    def add_all(self, values):
        self.added.extend(values)


class FakeSessionFactory:
    def __init__(self, scalar_results):
        self.session = FakeSession(scalar_results)

    def __call__(self):
        return self.session


async def test_graph_backfill_skips_documents_with_unfinished_replace_events():
    first, second = uuid.uuid4(), uuid.uuid4()
    factory = FakeSessionFactory([[first, second], [first]])

    report = await PostgresGraphBackfill(factory).enqueue()

    assert report.selected == 2
    assert report.queued == 1
    assert report.skipped == 1
    assert [event.document_id for event in factory.session.added] == [second]
    assert factory.session.added[0].operation == "replace"


async def test_graph_backfill_dry_run_does_not_write_events():
    document_id = uuid.uuid4()
    factory = FakeSessionFactory([[document_id], []])

    report = await PostgresGraphBackfill(factory).enqueue(dry_run=True)

    assert report.dry_run is True
    assert report.queued == 1
    assert factory.session.added == []


async def test_graph_backfill_force_queues_all_without_deduplication_query():
    first, second = uuid.uuid4(), uuid.uuid4()
    factory = FakeSessionFactory([[first, second]])

    report = await PostgresGraphBackfill(factory).enqueue(force=True)

    assert report.queued == 2
    assert report.skipped == 0
    assert [event.document_id for event in factory.session.added] == [first, second]
