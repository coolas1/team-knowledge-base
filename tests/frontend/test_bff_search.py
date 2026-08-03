import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from src.engine.interface import KnowledgeQueryResult, KnowledgeSource
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    fake = InProcessEngineClient(FakeKnowledgeBase())
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: fake
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_search(client):
    res = client.post("/api/search", json={"query": "acme"})
    assert res.status_code == 200
    out = res.json()
    assert "chunks" in out and "related_entities" in out and "related_docs" in out


def test_search_original_route_forwards_hindsight_options(monkeypatch):
    async def noop():
        pass

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

    service = FakeQueryService()
    engine = InProcessEngineClient(FakeKnowledgeBase(), query_service=service)
    monkeypatch.setattr(deps, "startup", noop)
    monkeypatch.setattr(deps, "shutdown", noop)
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: engine
    try:
        with TestClient(app_mod.app) as test_client:
            response = test_client.post(
                "/api/search",
                json={
                    "query": "分析项目进展",
                    "top_k": 4,
                    "mode": "deep",
                    "needs_answer": True,
                },
            )
    finally:
        app_mod.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["answer"] == "grounded answer"
    assert response.json()["chunks"][0]["memory_id"] == "m1"
    assert service.request.mode == "deep"
    assert service.request.top_k == 4
    assert service.request.needs_answer is True


def test_search_rejects_blank_query(client):
    response = client.post("/api/search", json={"query": "   "})
    assert response.status_code == 422


def test_search_rejects_invalid_mode(client):
    response = client.post("/api/search", json={"query": "acme", "mode": "turbo"})
    assert response.status_code == 422
