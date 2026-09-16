"""未处理异常的 /api 错误信封（见 openspec specs/webapp）。"""

import logging

import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
from tests.conftest import FakeKnowledgeBase

APP_LOGGER = "src.frontend.webapp.server.app"


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    kb = FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: kb
    app_mod.app.dependency_overrides[deps.get_plugin] = lambda: None
    # Starlette 的 ServerErrorMiddleware 送回处理器写的响应之后仍会把异常
    # 重新抛出（交给服务器记日志），所以这里要关掉 TestClient 的重抛才能
    # 看到真实客户端拿到的响应——生产里 uvicorn 也照常把它记进日志。
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        yield c, kb
    app_mod.app.dependency_overrides.clear()


def _raise_on_list(client, monkeypatch):
    """让 /api/documents 抛出未处理异常（该路由本身不捕获异常）。"""
    c, kb = client

    async def boom(*_args, **_kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(kb, "list_documents", boom)
    return c


def test_unhandled_api_error_returns_the_structured_envelope(client, monkeypatch):
    c = _raise_on_list(client, monkeypatch)

    res = c.get("/api/documents")

    assert res.status_code == 500
    detail = res.json()["detail"]
    assert detail["code"] == "internal_error"
    assert detail["message"]
    assert detail["suggestion"]
    assert detail["retryable"] is True


def test_envelope_replaces_the_bare_server_error(client, monkeypatch):
    """不是 uvicorn 那句裸文本，也不是空 body。"""
    c = _raise_on_list(client, monkeypatch)

    res = c.get("/api/documents")

    assert res.headers["content-type"].startswith("application/json")
    assert isinstance(res.json()["detail"], dict)


def test_request_id_is_returned_and_logged(client, monkeypatch, caplog):
    c = _raise_on_list(client, monkeypatch)

    with caplog.at_level(logging.ERROR, logger=APP_LOGGER):
        res = c.get("/api/documents")

    request_id = res.json()["detail"]["request_id"]
    assert request_id
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert request_id in log_text
    # traceback 与编号在同一条记录里，事后才定位得到
    assert caplog.records[-1].exc_info is not None
    assert "RuntimeError" in caplog.text


def test_request_ids_differ_between_failures(client, monkeypatch):
    """编号是每次请求的，不是进程级的常量。"""
    c = _raise_on_list(client, monkeypatch)

    first = c.get("/api/documents").json()["detail"]["request_id"]
    second = c.get("/api/documents").json()["detail"]["request_id"]

    assert first != second


def test_explicit_http_errors_keep_their_shape(client):
    """显式 404 不被信封改写（客户端契约不变）。"""
    c, _ = client

    res = c.get("/api/documents/00000000-0000-0000-0000-000000000000")

    assert res.status_code == 404
    assert isinstance(res.json()["detail"], str)


def test_structured_upload_errors_keep_their_shape(client):
    """上传路径自己的结构化 detail 原样保留。"""
    c, _ = client

    res = c.post(
        "/api/documents/upload",
        files={"file": ("bad.exe", b"x", "application/octet-stream")},
    )

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] == "unsupported_file_type"
    assert detail["retryable"] is False
