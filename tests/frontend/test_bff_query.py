import pytest
from fastapi.testclient import TestClient

from src.agent.engine_client import InProcessEngineClient
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource
from src.frontend.webapp.server import app as app_mod, deps
from tests.conftest import FakeKnowledgeBase


class FakeQueryService:
    def __init__(self):
        self.request = None

    async def query(self, request):
        self.request = request
        return KnowledgeQueryResult(
            strategy_used="reflect",
            answer="reflected answer",
            sources=[
                KnowledgeSource(
                    memory_id="m1",
                    memory_type="chunk",
                    doc_id="d1",
                    title="Doc",
                    chunk_text="context",
                )
            ],
        )


@pytest.fixture
def query_client(monkeypatch):
    async def noop():
        pass

    service = FakeQueryService()
    engine = InProcessEngineClient(FakeKnowledgeBase(), query_service=service)
    monkeypatch.setattr(deps, "startup", noop)
    monkeypatch.setattr(deps, "shutdown", noop)
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: engine
    with TestClient(app_mod.app) as client:
        yield client, service
    app_mod.app.dependency_overrides.clear()


def test_query_route_forwards_hindsight_options(query_client):
    client, service = query_client
    response = client.post(
        "/api/query",
        json={
            "query": "where is Acme?",
            "strategy": "reflect",
            "mode": "fast",
            "top_k": 4,
            "needs_answer": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "reflected answer"
    assert service.request.strategy == "reflect"
    assert service.request.mode == "fast"
    assert service.request.top_k == 4


def test_query_route_returns_503_when_hindsight_is_disabled(monkeypatch):
    async def noop():
        pass

    engine = InProcessEngineClient(FakeKnowledgeBase())
    monkeypatch.setattr(deps, "startup", noop)
    monkeypatch.setattr(deps, "shutdown", noop)
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: engine
    try:
        with TestClient(app_mod.app) as client:
            response = client.post("/api/query", json={"query": "acme"})
    finally:
        app_mod.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "Hindsight" in response.json()["detail"]


def test_query_route_validates_options(query_client):
    client, _ = query_client
    response = client.post(
        "/api/query", json={"query": "acme", "strategy": "unsupported"}
    )
    assert response.status_code == 422


def test_query_route_rejects_blank_query(query_client):
    client, _ = query_client
    response = client.post("/api/query", json={"query": "   "})
    assert response.status_code == 422
