import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
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
    res = client.post("/search", json={"query": "acme"})
    assert res.status_code == 200
    out = res.json()
    assert "chunks" in out and "related_entities" in out and "related_docs" in out
