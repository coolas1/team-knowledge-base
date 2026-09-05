import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from src.frontend.webapp.server import app as app_mod, deps
from src.frontend.webapp.server import routes_agent
from src.agent.codex.plugin import build_plugin
from src.agent.engine_client import InProcessEngineClient
from config.schema import load_config
from tests.conftest import FakeKnowledgeBase


class FakeLlm:
    async def complete(self, prompt):
        return "ANSWER FROM LLM"


@pytest.fixture
def client(monkeypatch):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    # plugin is accessed directly via deps.get_plugin() inside _find_skill
    plugin = build_plugin(load_config("config/app.yaml"))
    monkeypatch.setattr(deps, "get_plugin", lambda: plugin)
    # engine + llm injected via Depends
    fake_engine = InProcessEngineClient(FakeKnowledgeBase())
    fake_llm = FakeLlm()
    app_mod.app.dependency_overrides[deps.get_engine] = lambda: fake_engine
    app_mod.app.dependency_overrides[deps.get_llm] = lambda: fake_llm
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_agent_ask(client):
    res = client.post("/api/agent/ask", json={"query": "where is Acme?"})
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "知识库中未找到与该问题相关的内容。"
    assert out["query"] == "where is Acme?"


def _mock_pi(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        routes_agent,
        "_pi_client",
        lambda: httpx.AsyncClient(transport=transport),
    )


def test_agent_session_proxy(client, monkeypatch):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/sessions"
        return httpx.Response(201, json={"id": "session-1", "messageCount": 0})

    _mock_pi(monkeypatch, handler)
    response = client.post("/api/agent/sessions")

    assert response.status_code == 201
    assert response.json()["id"] == "session-1"


def test_agent_session_memory_forget_proxy(client, monkeypatch):
    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == "/v1/sessions/session-1/memory"
        return httpx.Response(
            200,
            json={"sessionId": "session-1", "cancelledJobs": 1, "deletedDocuments": 2},
        )

    _mock_pi(monkeypatch, handler)
    response = client.delete("/api/agent/sessions/session-1/memory")

    assert response.status_code == 200
    assert response.json()["deletedDocuments"] == 2


def test_agent_message_proxy_streams_sse(client, monkeypatch):
    stream = (
        'event: message.accepted\ndata: {"type":"message.accepted","turnId":"t1"}\n\n'
        'event: tool.start\ndata: {"type":"tool.start","toolName":"tkb_list_documents"}\n\n'
        'event: message.completed\ndata: {"type":"message.completed","answer":"23"}\n\n'
    ).encode()

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/sessions/session-1/messages"
        assert (
            request.read() == b'{"message":"count files","clientMessageId":"client-1"}'
        )
        return httpx.Response(
            200,
            stream=httpx.ByteStream(stream),
            headers={"content-type": "text/event-stream"},
        )

    _mock_pi(monkeypatch, handler)
    response = client.post(
        "/api/agent/sessions/session-1/messages",
        json={"message": "count files", "clientMessageId": "client-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "tkb_list_documents" in response.text
    assert "message.accepted" in response.text
    assert "message.completed" in response.text


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_agent_message_proxy_preserves_terminal_status(client, monkeypatch, status):
    stream = (
        'event: message.accepted\ndata: {"type":"message.accepted","turnId":"t1"}\n\n'
        f'event: message.failed\ndata: {{"type":"message.failed","status":"{status}","code":"{status}"}}\n\n'
    ).encode()

    def handler(request):
        return httpx.Response(
            200,
            stream=httpx.ByteStream(stream),
            headers={"content-type": "text/event-stream"},
        )

    _mock_pi(monkeypatch, handler)
    response = client.post(
        "/api/agent/sessions/session-1/messages",
        json={"message": "count files", "clientMessageId": "client-1"},
    )

    assert response.status_code == 200
    assert f'"status":"{status}"' in response.text


def test_agent_message_proxy_keeps_legacy_request_shape(client, monkeypatch):
    def handler(request):
        assert request.read() == b'{"message":"legacy"}'
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"event: done\ndata: {}\n\n"),
            headers={"content-type": "text/event-stream"},
        )

    _mock_pi(monkeypatch, handler)
    response = client.post(
        "/api/agent/sessions/session-1/messages", json={"message": "legacy"}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sse_relay_closes_upstream_when_downstream_disconnects():
    async def chunks():
        yield b"event: message.accepted\ndata: {}\n\n"
        yield b"event: message.completed\ndata: {}\n\n"

    response = AsyncMock()
    response.aiter_raw = chunks
    client = AsyncMock()
    relay = routes_agent._relay_sse(response, client)
    assert await anext(relay) == b"event: message.accepted\ndata: {}\n\n"
    await relay.aclose()
    response.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()


def test_agent_proxy_returns_503_when_runtime_is_unavailable(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("offline", request=request)

    _mock_pi(monkeypatch, handler)
    response = client.post("/api/agent/sessions")

    assert response.status_code == 503
    assert response.json()["detail"] == "Pi Agent 当前不可用"


def test_agent_proxy_rejects_invalid_session_id(client):
    response = client.post(
        "/api/agent/sessions/bad!id/messages",
        json={"message": "hello"},
    )
    assert response.status_code == 400


def test_agent_ingest_summarize(client):
    res = client.post(
        "/api/agent/ingest-summarize",
        files={"file": ("r.md", b"# T\n\nAcme is in Building A.", "text/markdown")},
    )
    assert res.status_code == 200
    out = res.json()
    assert out["doc"]["title"] == "r.md"
    assert out["summary"] == "ANSWER FROM LLM"


def test_config_get(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["engine"]["impl"] == "graphrag"


def test_config_put_validates(client, tmp_path, monkeypatch):
    # Point config write at a temp file so we don't clobber the real app.yaml.
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put(
        "/api/config",
        json={
            "engine": {"impl": "graphrag", "config": "config/engine/graphrag"},
            "agent": {
                "harness": "codex",
                "skills": ["search_and_answer"],
                "memory": {"impl": None},
            },
            "frontend": {"impl": "webapp"},
            "webapp": {"engine_access": "mcp"},
        },
    )
    assert res.status_code == 200
    assert res.json()["webapp"]["engine_access"] == "mcp"


def test_config_put_rejects_invalid(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/api/config", json={"webapp": {"engine_access": "bogus"}})
    assert res.status_code == 422
