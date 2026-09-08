import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod
from src.frontend.webapp.server import deps


@pytest.fixture
def client(monkeypatch):
    # Bypass real engine build: no-op lifespan (health needs no engine).
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    with TestClient(app_mod.app) as c:
        yield c


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
