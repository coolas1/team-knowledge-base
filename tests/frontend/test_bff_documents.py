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
    kb = FakeKnowledgeBase()
    fake = InProcessEngineClient(kb)
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: fake
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    with TestClient(app_mod.app) as c:
        yield c, kb
    app_mod.app.dependency_overrides.clear()


def test_list_documents(client):
    c, _ = client
    res = c.get("/documents")
    assert res.status_code == 200
    assert "items" in res.json()


def test_upload_document(client):
    c, kb = client
    res = c.post(
        "/documents/upload",
        files={"file": ("r.md", b"# T\n\nAcme", "text/markdown")},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "r.md"
    assert list(kb.raw.values())[0] == b"# T\n\nAcme"


def test_delete_document(client):
    c, _ = client
    res = c.delete("/documents/abc")
    assert res.status_code == 200
    assert res.json() == {"removed": "abc"}


def test_get_document_not_found(client):
    c, _ = client
    # FakeKnowledgeBase.get_document returns None -> EngineClient returns {"error": ...}
    res = c.get("/documents/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
