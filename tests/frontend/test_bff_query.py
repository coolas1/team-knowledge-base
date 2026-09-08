"""Test the /api/query route (Hindsight KnowledgeQuery endpoint)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource
from tests.conftest import FakeKnowledgeBase


class FakeQueryService:
    request = None

    async def query(self, request):
        self.request = request
        return KnowledgeQueryResult(
            strategy_used="reflect",
            answer="grounded answer",
            sources=[
                KnowledgeSource(
                    memory_id="m1",
                    memory_type="world",
                    doc_id="d1",
                    title="Doc",
                    chunk_text="context",
                )
            ],
        )


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    service = FakeQueryService()
    app_mod.app.dependency_overrides[deps.get_query] = lambda: service
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_query_returns_reflect_result(client):
    res = client.post(
        "/api/query",
        json={"query": "分析项目进展", "strategy": "reflect", "mode": "deep"},
    )
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "grounded answer"
    assert out["strategy_used"] == "reflect"
    assert out["sources"][0]["memory_id"] == "m1"


def test_query_rejects_blank_query(client):
    res = client.post("/api/query", json={"query": "   "})
    assert res.status_code == 422


def test_query_returns_503_when_no_service(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    app_mod.app.dependency_overrides[deps.get_query] = lambda: None
    try:
        with TestClient(app_mod.app) as c:
            res = c.post("/api/query", json={"query": "test"})
            assert res.status_code == 503
    finally:
        app_mod.app.dependency_overrides.clear()
