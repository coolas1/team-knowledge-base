import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
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
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: kb
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_full_graph(client):
    res = client.get("/api/graph/full")
    assert res.status_code == 200
    assert res.json()["nodes"][0]["name"] == "Acme"


def test_entity_graph(client):
    res = client.get("/api/graph/entity/Acme")
    assert res.status_code == 200


def test_neighbors(client):
    res = client.get("/api/graph/neighbors/Acme?hops=2")
    assert res.status_code == 200


def test_neighbors_threads_hops_to_the_backend(client):
    c = client
    kb = app_mod.app.dependency_overrides[deps.get_kb]()
    seen = []

    async def get_neighbors(entity, hops=2):
        seen.append((entity, hops))
        return kb.graph

    kb.get_neighbors = get_neighbors

    assert c.get("/api/graph/neighbors/Acme?hops=1").status_code == 200
    assert c.get("/api/graph/neighbors/Acme").status_code == 200

    assert seen == [("Acme", 1), ("Acme", 2)]  # 透传 hops，默认 2


def test_neighbors_rejects_out_of_range_hops(client):
    c = client
    assert c.get("/api/graph/neighbors/Acme?hops=0").status_code == 422
    assert c.get("/api/graph/neighbors/Acme?hops=9").status_code == 422
