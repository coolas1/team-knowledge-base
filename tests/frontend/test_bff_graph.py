import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from src.agent.engine_client import InProcessEngineClient
from src.engine.interface import GraphData, GraphNode
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    kb = FakeKnowledgeBase()
    kb.graph = GraphData(nodes=[GraphNode(name="Acme", type="Company")])
    fake = InProcessEngineClient(kb)
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: fake
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_full_graph(client):
    res = client.get("/graph/full")
    assert res.status_code == 200
    assert res.json()["nodes"][0]["name"] == "Acme"


def test_entity_graph(client):
    res = client.get("/graph/entity/Acme")
    assert res.status_code == 200


def test_neighbors(client):
    res = client.get("/graph/neighbors/Acme?hops=2")
    assert res.status_code == 200
