from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.frontend.webapp.server import routes_ppt
from src.engine.trusted_scope import ScopeBinding


@pytest.fixture
def setup(monkeypatch):
    store = AsyncMock()
    store.create.return_value = "job"
    store.get.return_value = {"id": "job", "revision": 2}
    monkeypatch.setattr(routes_ppt, "get_store", lambda: store)
    monkeypatch.setattr(
        routes_ppt.deps, "_binding", lambda request: ScopeBinding(bank_id="A")
    )
    app = FastAPI()
    app.include_router(routes_ppt.router, prefix="/api")
    with TestClient(app) as client:
        yield client, store


def test_create_passes_trusted_binding_and_returns_promptly(setup):
    client, store = setup
    result = client.post(
        "/api/ppt/jobs",
        json={
            "spec": {
                "title": "deck",
                "style": "navy",
                "pages": [
                    {
                        "title": "one",
                        "points": ["fact"],
                        "layout": "timeline",
                        "notes": "notes",
                    }
                ],
            }
        },
    )
    assert result.status_code == 202
    assert result.json() == {"id": "job", "review_url": "/ppt/job"}
    assert store.create.call_args.args[1].bank_id == "A"
    assert store.create.call_args.args[2] == "default"


def test_revision_error_and_scope_denial_are_not_success(setup):
    client, store = setup
    store.control.side_effect = ValueError("Stale PPT revision")
    assert (
        client.post(
            "/api/ppt/jobs/job/control",
            json={"revision": 1, "action": "approve_sample"},
        ).status_code
        == 409
    )
    store.get.side_effect = PermissionError("private details")
    result = client.get("/api/ppt/jobs/job")
    assert result.status_code == 403 and "private details" not in result.text
    store.file.side_effect = PermissionError()
    assert client.get("/api/ppt/jobs/job/pages/1").status_code == 403
    assert client.get("/api/ppt/jobs/job/download").status_code == 403


async def test_mcp_approval_only_returns_review_link_and_preview_is_image(
    tmp_path, monkeypatch
):
    import io
    from PIL import Image
    from mcp.server.fastmcp import FastMCP
    from src.agent.ppt import tools

    image = tmp_path / "slide.png"
    out = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(out, format="PNG")
    image.write_bytes(out.getvalue())
    store = AsyncMock()
    store.get.return_value = {"revision": 2, "pages": [{}]}
    store.file.return_value = image
    monkeypatch.setattr(tools, "get_store", lambda: store)
    mcp = FastMCP("ppt-test")
    tools.register(mcp, lambda: ScopeBinding())
    result = await mcp.call_tool("approve_ppt", {"identifier": "job", "revision": 2})
    assert "requires_ui_approval" in str(result)
    store.control.assert_not_awaited()
    preview = await mcp.call_tool("preview_ppt", {"identifier": "job", "page": 1})
    assert any(getattr(block, "type", None) == "image" for block in preview)
