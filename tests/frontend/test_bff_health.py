import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod
from src.frontend.webapp.server import deps
from src.agent.engine_client import InProcessEngineClient
from tests.conftest import FakeKnowledgeBase


@pytest.fixture
def client(monkeypatch):
    # Bypass real engine build: no-op lifespan + dependency overrides.
    async def _noop_startup():
        pass

    async def _noop_shutdown():
        pass

    monkeypatch.setattr(deps, "startup", _noop_startup)
    monkeypatch.setattr(deps, "shutdown", _noop_shutdown)
    fake = InProcessEngineClient(FakeKnowledgeBase())
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: fake
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
