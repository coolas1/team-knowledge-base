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
    kb = FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: kb
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

    engine = app_mod.app.dependency_overrides[deps.get_kb]()
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
    res = c.post("/api/documents/44444444-5555-6666-7777-888888888888/retry")

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
    res = c.put(
        "/api/documents/55555555-6666-7777-8888-999999999999/content",
        json={"content": "new"},
    )
    assert res.status_code == 404


def test_list_document_versions(client):
    c, kb = client
    doc_id = "66666666-7777-8888-9999-000000000000"

    async def list_versions(doc_id):
        return [{"id": doc_id, "version_number": 1, "is_current": True}]

    kb.list_versions = list_versions
    res = c.get(f"/api/documents/{doc_id}/versions")

    assert res.status_code == 200
    assert res.json()["versions"][0]["version_number"] == 1


def test_diff_document_versions(client):
    c, kb = client
    doc_id = "77777777-8888-9999-0000-111111111111"

    async def diff_versions(doc_id, from_version, to_version):
        return {
            "doc_id": doc_id,
            "from_version": from_version,
            "to_version": to_version,
            "changes": [],
        }

    kb.diff_versions = diff_versions
    res = c.get(
        f"/api/documents/{doc_id}/versions/diff",
        params={"from_version": 1, "to_version": 2},
    )

    assert res.status_code == 200
    assert res.json()["to_version"] == 2


def test_delete_document(client):
    c, _ = client
    doc_id = "88888888-9999-0000-1111-222222222222"
    res = c.delete(f"/api/documents/{doc_id}")
    assert res.status_code == 200
    assert res.json() == {"removed": doc_id}


def test_get_document_not_found(client):
    c, _ = client
    # FakeKnowledgeBase.get_document returns None -> 404
    res = c.get("/api/documents/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_upload_batch_mixes_success_and_failure(client):
    c, kb = client
    res = c.post(
        "/api/documents/upload/batch",
        files=[
            ("files", ("good.md", b"# ok", "text/markdown")),
            ("files", ("bad.zip", b"x", "application/zip")),
            ("files", ("empty.md", b"", "text/markdown")),
        ],
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["ok"] for i in items] == [True, False, False]
    assert items[0]["document"]["title"] == "good.md"
    assert items[0]["document"]["status"] == "indexed"
    assert items[1]["error"]["code"] == "unsupported_file_type"
    assert items[2]["error"]["code"] == "empty_file"
    assert list(kb.raw.values()) == [b"# ok"]


def test_upload_batch_rejects_empty_request(client):
    c, _ = client
    # 0 个文件在 FastAPI 参数校验层被拒绝（required File 字段缺失）
    res = c.post("/api/documents/upload/batch", files=[])
    assert res.status_code == 422


def test_upload_rejects_oversized_file_with_413(client, monkeypatch):
    from src.frontend.webapp.server import routes_documents

    # 补丁打在路由模块实际读取的 settings 对象上（避免其他测试
    # reload settings 模块导致的实例错位）。
    monkeypatch.setattr(routes_documents.settings, "kb_max_upload_bytes", 16)
    c, kb = client
    res = c.post(
        "/api/documents/upload",
        files={"file": ("big.md", b"# " + b"x" * 100, "text/markdown")},
    )

    assert res.status_code == 413
    detail = res.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert "16 字节" in detail["message"]
    assert "KB_MAX_UPLOAD_BYTES" in detail["suggestion"]
    assert not kb.docs  # 未创建文档


def test_upload_batch_isolates_oversized_file(client, monkeypatch):
    from src.frontend.webapp.server import routes_documents

    monkeypatch.setattr(routes_documents.settings, "kb_max_upload_bytes", 16)
    c, kb = client
    res = c.post(
        "/api/documents/upload/batch",
        files=[
            ("files", ("small.md", b"# ok", "text/markdown")),
            ("files", ("big.md", b"x" * 100, "text/markdown")),
        ],
    )

    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["ok"] is True
    assert items[0]["document"]["title"] == "small.md"
    assert items[1]["ok"] is False
    assert items[1]["error"]["code"] == "file_too_large"
    assert items[1]["error"]["filename"] == "big.md"
    assert list(kb.raw.values()) == [b"# ok"]  # 超限文件隔离，不影响其余文件


def test_malformed_document_id_is_rejected_with_422(client):
    c, _ = client
    for path in [
        "/api/documents/not-a-uuid",
        "/api/documents/not-a-uuid/versions",
        "/api/documents/not-a-uuid/versions/diff?from_version=1&to_version=2",
    ]:
        res = c.get(path)
        assert res.status_code == 422, path
    assert c.post("/api/documents/not-a-uuid/retry").status_code == 422
    assert c.delete("/api/documents/not-a-uuid").status_code == 422
    assert (
        c.put("/api/documents/not-a-uuid/content", json={"content": "x"}).status_code
        == 422
    )


def test_well_formed_missing_document_still_404(client):
    c, kb = client
    missing = "11111111-2222-3333-4444-555555555555"

    async def list_versions_raises(doc_id):
        raise ValueError(f"文档不存在: {doc_id}")

    kb.list_versions = list_versions_raises

    res = c.get(f"/api/documents/{missing}")
    assert res.status_code == 404
    res = c.get(f"/api/documents/{missing}/versions")
    assert res.status_code == 404


def test_edit_content_on_non_markdown_is_400_with_reason(client):
    from src.engine.interface import DocumentRef

    c, kb = client
    doc_id = "22222222-3333-4444-5555-666666666666"
    kb.docs[doc_id] = DocumentRef(
        id=doc_id, title="report.pdf", file_type="pdf", status="indexed"
    )

    res = c.put(f"/api/documents/{doc_id}/content", json={"content": "new text"})

    assert res.status_code == 400
    assert "Markdown" in res.json()["detail"]


def test_edit_content_on_missing_document_is_404(client):
    c, _ = client
    missing = "33333333-4444-5555-6666-777777777777"
    res = c.put(f"/api/documents/{missing}/content", json={"content": "new text"})
    assert res.status_code == 404


def test_spa_fallback_excludes_mcp_and_serves_client_routes(client, monkeypatch, tmp_path):
    from src.frontend.webapp.server import app as app_mod

    index = tmp_path / "index.html"
    index.write_text("<html>spa-shell</html>", encoding="utf-8")
    monkeypatch.setattr(app_mod, "SPA_DIST", tmp_path)
    c, _ = client

    mcp = c.get("/mcp")
    assert mcp.status_code == 404
    assert "spa-shell" not in mcp.text  # MCP 端点不被 SPA 外壳掩盖

    spa = c.get("/no-such-client-route")
    assert spa.status_code == 200
    assert "spa-shell" in spa.text
