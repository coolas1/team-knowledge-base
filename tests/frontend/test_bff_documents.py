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
    res = c.get("/api/documents")
    assert res.status_code == 200
    assert "items" in res.json()


def test_list_documents_hides_internal_conversation_sources(client):
    c, kb = client
    from src.engine.interface import DocumentRef

    kb.docs["file-1"] = DocumentRef(
        id="file-1", title="visible.md", file_type="markdown", status="indexed"
    )
    kb.docs["conversation-1"] = DocumentRef(
        id="conversation-1",
        title="Conversation turn",
        file_type="conversation",
        status="indexed",
    )

    result = c.get("/api/documents").json()

    assert [item["id"] for item in result["items"]] == ["file-1"]
    assert result["total"] == 1


def test_upload_document(client):
    c, kb = client
    res = c.post(
        "/api/documents/upload",
        files={"file": ("r.md", b"# T\n\nAcme", "text/markdown")},
    )
    assert res.status_code == 200
    assert res.json()["title"] == "r.md"
    assert list(kb.raw.values())[0] == b"# T\n\nAcme"


def test_upload_rejects_unsupported_file_with_guidance(client):
    c, _ = client
    res = c.post(
        "/api/documents/upload",
        files={"file": ("archive.zip", b"content", "application/zip")},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "unsupported_file_type"
    assert ".pdf" in res.json()["detail"]["suggestion"]
    assert res.json()["detail"]["retryable"] is False


def test_upload_rejects_empty_file_with_guidance(client):
    c, _ = client
    res = c.post(
        "/api/documents/upload",
        files={"file": ("empty.md", b"", "text/markdown")},
    )

    assert res.status_code == 400
    assert res.json()["detail"] == {
        "code": "empty_file",
        "message": "文件内容为空",
        "suggestion": "请确认文件包含内容，保存后重新选择该文件。",
        "retryable": False,
    }


def test_upload_service_failure_is_retryable(client, monkeypatch):
    c, _ = client

    async def fail_ingest(_name, _data):
        raise RuntimeError("database unavailable")

    engine = app_mod.app.dependency_overrides[deps.get_engine]()
    monkeypatch.setattr(engine, "ingest", fail_ingest)

    res = c.post(
        "/api/documents/upload",
        files={"file": ("retry.md", b"content", "text/markdown")},
    )

    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "upload_service_unavailable"
    assert res.json()["detail"]["retryable"] is True


def test_retry_failed_document(client):
    c, kb = client
    uploaded = c.post(
        "/api/documents/upload",
        files={"file": ("retry.md", b"content", "text/markdown")},
    ).json()
    kb.docs[uploaded["id"]].status = "failed"

    res = c.post(f"/api/documents/{uploaded['id']}/retry")

    assert res.status_code == 200
    assert res.json()["status"] == "pending"


def test_retry_missing_document_has_upload_guidance(client):
    c, _ = client
    res = c.post("/api/documents/missing/retry")

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "document_not_retryable"
    assert "重新选择原文件" in res.json()["detail"]["suggestion"]


def test_edit_document_content(client):
    c, kb = client
    uploaded = c.post(
        "/api/documents/upload",
        files={"file": ("r.md", b"old", "text/markdown")},
    ).json()

    res = c.put(
        f"/api/documents/{uploaded['id']}/content",
        json={"content": "# Updated\n\nNew content"},
    )

    assert res.status_code == 200
    assert res.json()["status"] == "pending"
    assert kb.raw[uploaded["id"]] == b"# Updated\n\nNew content"


def test_edit_document_content_not_found(client):
    c, _ = client
    res = c.put("/api/documents/missing/content", json={"content": "new"})
    assert res.status_code == 404


def test_delete_document(client):
    c, _ = client
    res = c.delete("/api/documents/abc")
    assert res.status_code == 200
    assert res.json() == {"removed": "abc"}


def test_get_document_not_found(client):
    c, _ = client
    # FakeKnowledgeBase.get_document returns None -> EngineClient returns {"error": ...}
    res = c.get("/api/documents/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
