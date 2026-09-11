import asyncio
import contextlib
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from src.frontend.webapp.server import app as app_mod, deps
from src.frontend.webapp.server import routes_agent
from src.agent.loader import PluginLoader
from src.engine.interface import NOT_FOUND_ANSWER, RecallChunk, RecallResult
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
    plugin = PluginLoader().load(Path("src/agent/tkb"))
    monkeypatch.setattr(deps, "get_plugin", lambda: plugin)
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: FakeKnowledgeBase()
    app_mod.app.dependency_overrides[deps.get_llm] = lambda: FakeLlm()
    with TestClient(app_mod.app) as c:
        yield c
    app_mod.app.dependency_overrides.clear()


def test_agent_ask_answers_from_llm_when_recall_clears_the_gate(client):
    # Acme fixture above the 0.45 semantic floor: the found-chunks → LLM
    # answer path must be exercised at the BFF level.
    kb = FakeKnowledgeBase()
    kb.recall_result = RecallResult(
        chunks=[
            RecallChunk(
                doc_id="doc-acme",
                title="acme.md",
                chunk_text="Acme is in Building A.",
                reranker_score=0.9,
                vector_score=0.9,
            )
        ]
    )
    app_mod.app.dependency_overrides[deps.get_kb] = lambda: kb

    res = client.post("/api/agent/ask", json={"query": "where is Acme?"})

    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "ANSWER FROM LLM"
    assert out["query"] == "where is Acme?"


def test_agent_ask_returns_not_found_when_recall_finds_nothing(client):
    res = client.post("/api/agent/ask", json={"query": "where is Acme?"})
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == NOT_FOUND_ANSWER
    assert out["query"] == "where is Acme?"


def test_agent_ingest_summarize(client):
    res = client.post(
        "/api/agent/ingest-summarize",
        files={"file": ("r.md", b"# T\n\nAcme is in Building A.", "text/markdown")},
    )
    assert res.status_code == 200
    out = res.json()
    assert out["doc"]["title"] == "r.md"
    assert out["summary"] == "ANSWER FROM LLM"


def _mock_pi(monkeypatch, handler):
    """Route pi-agent traffic to a MockTransport.

    Returns a dict capturing the read timeout production code asks the client
    factory for, so the per-route timeout policy is asserted where it is
    decided rather than re-declared by the test.
    """
    transport = httpx.MockTransport(handler)
    captured: dict[str, float | None] = {}

    def factory(read_timeout: float | None = None):
        captured["read_timeout"] = read_timeout
        return httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                connect=5.0, read=read_timeout, write=30.0, pool=5.0
            ),
        )

    monkeypatch.setattr(routes_agent, "_pi_client", factory)
    return captured


@contextlib.contextmanager
def _stalling_sse_server(events: list[bytes], stall_seconds: float):
    """Local HTTP server that streams ``events`` with a gap between them.

    httpx enforces its read timeout on the real socket transport only —
    ``MockTransport`` never raises it, so a mocked pause proves nothing about
    the bound. Proving the relay is unbounded needs an actual socket that goes
    quiet for longer than the configured JSON read timeout.
    """
    ready = threading.Event()
    state: dict[str, int] = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            for index, event in enumerate(events):
                if index:
                    await asyncio.sleep(stall_seconds)
                writer.write(event)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    async def serve():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        state["port"] = server.sockets[0].getsockname()[1]
        ready.set()
        async with server:
            await server.serve_forever()

    loop = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(serve())
        except RuntimeError:
            pass  # loop stopped during teardown

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(timeout=5), "stalling SSE test server did not start"
    try:
        yield state["port"]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def test_agent_session_proxy(client, monkeypatch):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/sessions"
        return httpx.Response(201, json={"id": "session-1", "messageCount": 0})

    _mock_pi(monkeypatch, handler)
    response = client.post("/api/agent/sessions")

    assert response.status_code == 201
    assert response.json()["id"] == "session-1"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/sessions"),
        ("GET", "/sessions/s1"),
        ("DELETE", "/sessions/s1"),
        ("DELETE", "/sessions/s1/memory"),
        ("POST", "/sessions/s1/cancel"),
        ("POST", "/sessions/s1/messages"),
    ],
)
def test_agent_scope_headers_cover_lifecycle(client, monkeypatch, method, path):
    from hashlib import sha256
    from config.schema import AppConfig
    from src.engine.trusted_scope import ScopeBinding

    monkeypatch.setattr(
        deps,
        "_app_config",
        AppConfig.model_validate(
            {
                "engine": {"memory": {"enabled": True, "features": {"scope": True}}},
            }
        ),
    )
    monkeypatch.setattr(
        deps.settings,
        "memory_scope_bindings",
        {
            sha256(b"trusted").hexdigest(): ScopeBinding(bank_id="A"),
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["x-tkb-scope-token"] == "trusted"
        assert "x-tkb-bank-id" not in request.headers
        if path.endswith("messages"):
            return httpx.Response(
                200, stream=httpx.ByteStream(b"event: done\ndata: {}\n\n")
            )
        return httpx.Response(200, json={"ok": True})

    _mock_pi(monkeypatch, handler)
    assert (
        client.request(
            method,
            f"/api/agent{path}",
            headers={"x-tkb-scope-token": "trusted", "x-tkb-bank-id": "forged"},
            json={"message": "hello"},
        ).status_code
        == 200
    )
    assert (
        client.request(
            method,
            f"/api/agent{path}",
            headers={"x-tkb-scope-token": "forged"},
            json={"message": "hello"},
        ).status_code
        == 403
    )
    assert len(calls) == 1


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


# ── Bounded non-streaming proxy (app-deployment spec) ────────────────


def test_agent_read_timeout_defaults_and_honours_override(monkeypatch):
    monkeypatch.delenv("PI_AGENT_READ_TIMEOUT_SECONDS", raising=False)
    assert routes_agent._agent_read_timeout() == 30.0

    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "12.5")
    assert routes_agent._agent_read_timeout() == 12.5

    # Unset-equivalent and explicit opt-out.
    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "")
    assert routes_agent._agent_read_timeout() == 30.0
    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "0")
    assert routes_agent._agent_read_timeout() is None

    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "soon")
    with pytest.raises(ValueError, match="PI_AGENT_READ_TIMEOUT_SECONDS"):
        routes_agent._agent_read_timeout()


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("post", "/api/agent/sessions", 201),
        ("get", "/api/agent/sessions", 200),
        ("get", "/api/agent/sessions/session-1", 200),
        ("delete", "/api/agent/sessions/session-1", 200),
        ("delete", "/api/agent/sessions/session-1/memory", 200),
        ("post", "/api/agent/sessions/session-1/cancel", 200),
    ],
)
def test_non_streaming_proxy_routes_are_read_bounded(
    client, monkeypatch, method, path, expected_status
):
    captured = _mock_pi(monkeypatch, lambda request: httpx.Response(200, json={}))
    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "7")

    response = getattr(client, method)(path)

    assert response.status_code == expected_status
    assert captured["read_timeout"] == 7


def test_streaming_route_is_not_bounded_by_the_json_read_timeout(client, monkeypatch):
    captured = _mock_pi(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            stream=httpx.ByteStream(b"event: done\ndata: {}\n\n"),
            headers={"content-type": "text/event-stream"},
        ),
    )
    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "7")

    response = client.post("/api/agent/sessions/session-1/messages", json={"message": "hi"})

    assert response.status_code == 200
    assert captured["read_timeout"] is None


def test_streaming_relay_survives_a_pause_longer_than_the_json_timeout(
    client, monkeypatch
):
    # The scenario from the spec: events keep arriving though the gap between
    # them exceeds the non-streaming read timeout. This drives the real client
    # against a real socket, so the read timeout would actually fire were the
    # relay bounded by it.
    events = [
        b'event: message.accepted\ndata: {"type":"message.accepted","turnId":"t1"}\n\n',
        b'event: message.completed\ndata: {"type":"message.completed"}\n\n',
    ]
    gap = 0.6
    monkeypatch.setenv("PI_AGENT_READ_TIMEOUT_SECONDS", "0.2")

    with _stalling_sse_server(events, gap) as port:
        monkeypatch.setenv("PI_AGENT_URL", f"http://127.0.0.1:{port}")
        started = time.monotonic()
        response = client.post(
            "/api/agent/sessions/session-1/messages", json={"message": "hi"}
        )
        elapsed = time.monotonic() - started

    assert response.status_code == 200
    # The gap really did exceed the JSON read timeout, so the assertions below
    # are about the unbounded relay and not about a fast response.
    assert elapsed > gap > 0.2
    assert "message.accepted" in response.text
    assert "message.completed" in response.text


def test_agent_proxy_returns_504_when_the_sidecar_never_answers(client, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("sidecar stalled", request=request)

    _mock_pi(monkeypatch, handler)
    response = client.get("/api/agent/sessions")

    assert response.status_code == 504
    assert response.json()["detail"] == "Pi Agent 响应超时"


def test_agent_proxy_keeps_503_distinct_from_the_504_timeout(client, monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("no connection", request=request)

    _mock_pi(monkeypatch, handler)
    response = client.get("/api/agent/sessions")

    # A connect-phase timeout is still an unreachable sidecar, and must not be
    # reported as a read timeout, which would send the user chasing the wrong
    # cause.
    assert response.status_code == 503
    assert response.json()["detail"] == "Pi Agent 当前不可用"


def test_config_get(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["engine"]["impl"] == "graphrag"


def test_config_put_validates(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put(
        "/api/config",
        json={
            "engine": {"impl": "graphrag", "config": "config/engine/graphrag"},
            "plugin": {"impl": "tkb"},
        },
    )

    assert res.status_code == 200
    assert res.json()["plugin"]["impl"] == "tkb"


def test_config_put_rejects_dropped_axes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put(
        "/api/config",
        json={
            "engine": {"impl": "graphrag", "config": "config/engine/graphrag"},
            "plugin": {"impl": "tkb"},
            "host": {"impl": "webapp"},
        },
    )
    assert res.status_code == 422


def test_config_put_rejects_invalid(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/api/config", json={"engine": {"impl": 123}})
    assert res.status_code == 422
