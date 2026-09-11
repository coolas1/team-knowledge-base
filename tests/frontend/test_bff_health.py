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


def test_missing_hashed_asset_does_not_get_the_spa_shell(client):
    # A stale cached shell references a bundle a later deploy removed. Handing
    # back index.html answers a JavaScript request with HTML; the recovery
    # document must be served instead.
    res = client.get("/assets/index-DOES-NOT-EXIST.js")

    assert res.status_code == 404
    assert "text/html" in res.headers["content-type"]
    assert "页面资源不可用" in res.text
    assert "<div id=\"root\">" not in res.text


def test_unknown_api_path_is_not_masked_by_the_shell(client):
    res = client.get("/api/definitely-not-a-route")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")


def test_mcp_is_not_masked_by_the_shell(client):
    # The MCP endpoint must answer JSON, never the SPA shell.
    res = client.get("/mcp/definitely-not-a-route")
    assert "text/html" not in res.headers.get("content-type", "")


def test_asset_miss_recovery_body_is_not_the_spa_shell(client):
    # Guards the carve-out itself: whatever else changes, a missing asset must
    # not be answered with something that looks like the app shell.
    res = client.get("/assets/nope.js")
    assert res.status_code == 404
    assert 'id="root"' not in res.text
