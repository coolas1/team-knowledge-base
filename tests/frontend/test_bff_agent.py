import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps
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
    res = client.post("/agent/ask", json={"query": "where is Acme?"})
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "ANSWER FROM LLM"
    assert out["query"] == "where is Acme?"


def test_agent_ingest_summarize(client):
    res = client.post(
        "/agent/ingest-summarize",
        files={"file": ("r.md", b"# T\n\nAcme is in Building A.", "text/markdown")},
    )
    assert res.status_code == 200
    out = res.json()
    assert out["doc"]["title"] == "r.md"
    assert out["summary"] == "ANSWER FROM LLM"


def test_config_get(client):
    res = client.get("/config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["engine"]["impl"] == "graphrag"


def test_config_put_validates(client, tmp_path, monkeypatch):
    # Point config write at a temp file so we don't clobber the real app.yaml.
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/config", json={
        "engine": {"impl": "graphrag", "config": "config/engine/graphrag"},
        "agent": {"harness": "codex", "skills": ["search_and_answer"], "memory": {"impl": None}},
        "frontend": {"impl": "webapp"},
        "webapp": {"engine_access": "mcp"},
    })
    assert res.status_code == 200
    assert res.json()["webapp"]["engine_access"] == "mcp"


def test_config_put_rejects_invalid(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.frontend.webapp.server.routes_config.CONFIG_PATH", tmp_path / "app.yaml"
    )
    res = client.put("/config", json={"webapp": {"engine_access": "bogus"}})
    assert res.status_code == 422
