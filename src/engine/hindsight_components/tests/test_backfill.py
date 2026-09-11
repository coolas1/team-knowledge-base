from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from src.engine.hindsight_components.backfill import (
    BackfillCandidate,
    HttpCandidateSource,
    run_backfill,
)
from src.engine.hindsight_components.types import RetainResult


class FakeSource:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    async def list_candidates(self, *, document_id=None, force=False):
        self.calls.append((document_id, force))
        return list(self.candidates)


class FakeStateStore:
    def __init__(self):
        self.states = []

    async def set_document_state(self, document_id, status, *, error_msg=None) -> None:
        self.states.append((document_id, status, error_msg))


class FakeService:
    def __init__(self, *, failing_id=None):
        self.failing_id = failing_id
        self.calls = []
        self.active = 0
        self.maximum = 0

    async def retain(self, **kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        if kwargs["document_id"] == self.failing_id:
            raise RuntimeError("LLM unavailable")
        return RetainResult(
            document_id=kwargs["document_id"],
            chunks=1,
            facts=2,
            observations=1,
            memories=4,
            links=3,
        )


def candidate(document_id: str) -> BackfillCandidate:
    return BackfillCandidate(
        document_id=document_id,
        title=f"{document_id}.md",
        content="weekly report",
        file_type="markdown",
    )


async def test_dry_run_only_lists_candidates():
    source = FakeSource([candidate("d1"), candidate("d2")])
    service = FakeService()
    states = FakeStateStore()

    report = await run_backfill(source, service, states, dry_run=True, document_id="d1")

    assert report.dry_run is True
    assert report.selected == 2
    assert [item.status for item in report.items] == ["pending", "pending"]
    assert source.calls == [("d1", False)]
    assert service.calls == []
    assert states.states == []


@pytest.mark.parametrize("outcome", ["degraded", "failed"])
async def test_backfill_does_not_count_incomplete_extraction_as_success(outcome):
    class IncompleteService(FakeService):
        async def retain(self, **kwargs):
            result = await super().retain(**kwargs)
            return replace(result, status=outcome, error_code="extraction_incomplete")

    report = await run_backfill(
        FakeSource([candidate("d1")]), IncompleteService(), FakeStateStore()
    )
    assert report.succeeded == 0
    assert report.failed == 1
    assert report.items[0].status == outcome
    assert report.items[0].error == "extraction_incomplete"


async def test_backfill_retains_raw_text_without_reindexing():
    source = FakeSource([candidate("d1")])
    service = FakeService()
    states = FakeStateStore()

    report = await run_backfill(source, service, states)

    assert report.succeeded == 1
    assert report.failed == 0
    assert report.items[0].memories == 4
    assert states.states == [("d1", "retaining", None)]
    assert service.calls == [
        {
            "document_id": "d1",
            "title": "d1.md",
            "content": "weekly report",
            "file_type": "markdown",
            "source_type": "historical-backfill",
        }
    ]


async def test_backfill_isolates_document_failures():
    source = FakeSource([candidate("d1"), candidate("d2")])
    service = FakeService(failing_id="d2")
    states = FakeStateStore()

    report = await run_backfill(source, service, states)

    assert report.succeeded == 1
    assert report.failed == 1
    assert states.states[-1] == ("d2", "failed", "LLM unavailable")


async def test_backfill_respects_concurrency():
    source = FakeSource([candidate(f"d{index}") for index in range(4)])
    service = FakeService()

    await run_backfill(source, service, FakeStateStore(), concurrency=2)

    assert service.maximum == 2


async def test_backfill_rejects_invalid_concurrency():
    with pytest.raises(ValueError, match="concurrency"):
        await run_backfill(
            FakeSource([]), FakeService(), FakeStateStore(), concurrency=0
        )


# -- HttpCandidateSource tests -----------------------------------------------


async def test_http_candidate_source_lists_candidates():
    def handler(request):
        assert request.url.path == "/api/engine/hindsight/backfill/candidates"
        assert request.url.params.get("force") == "true"
        return httpx.Response(
            200,
            json=[
                {
                    "document_id": "d1",
                    "title": "t.md",
                    "content": "text",
                    "file_type": "markdown",
                    "hindsight_status": None,
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    source = HttpCandidateSource("http://engine")
    source._client = httpx.AsyncClient(transport=transport, base_url="http://engine")

    candidates = await source.list_candidates(force=True)
    assert len(candidates) == 1
    assert candidates[0].document_id == "d1"
    assert candidates[0].content == "text"
    await source._client.aclose()


async def test_http_candidate_source_with_document_id():
    def handler(request):
        assert request.url.params.get("document_id") == "d1"
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    source = HttpCandidateSource("http://engine")
    source._client = httpx.AsyncClient(transport=transport, base_url="http://engine")

    candidates = await source.list_candidates(document_id="d1")
    assert candidates == []
    await source._client.aclose()
