import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_search(client):
    res = client.post("/api/search", json={"query": "acme"})
    assert res.status_code == 200
    out = res.json()
    assert "chunks" in out and "related_entities" in out and "related_docs" in out


def test_search_rejects_blank_query(client):
    response = client.post("/api/search", json={"query": "   "})
    assert response.status_code == 422


def test_search_rejects_invalid_mode(client):
    response = client.post("/api/search", json={"query": "acme", "mode": "turbo"})
    assert response.status_code == 422
