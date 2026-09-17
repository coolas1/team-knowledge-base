"""BFF 配置路由:读有效配置 + 来源,写运行时层。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.frontend.webapp.server import app as app_mod, deps, routes_config

COMMITTED = (
    "engine:\n"
    "  impl: graphrag\n"
    "  config: config/engine/graphrag\n"
    "  ingest:\n"
    "    chunk_concurrency: 4\n"
    "  memory:\n"
    "    enabled: true\n"
    "plugin:\n"
    "  impl: tkb\n"
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def _noop():
        pass

    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)
    committed = tmp_path / "app.yaml"
    committed.write_text(COMMITTED, encoding="utf-8")
    monkeypatch.setattr(routes_config, "CONFIG_PATH", committed)
    with TestClient(app_mod.app) as c:
        yield c, committed


def _body(client) -> dict:
    body = client.get("/api/config").json()
    body.pop("sources")
    return body


def test_get_returns_the_effective_configuration(client):
    c, _ = client
    res = c.get("/api/config")
    assert res.status_code == 200
    assert res.json()["engine"]["ingest"]["chunk_concurrency"] == 4
    assert res.json()["plugin"]["impl"] == "tkb"


def test_get_reports_a_source_per_key(client, monkeypatch):
    monkeypatch.setenv("TKB_ENGINE_INGEST_DOC_CONCURRENCY", "3")
    c, _ = client

    sources = c.get("/api/config").json()["sources"]
    assert sources["engine.ingest.chunk_concurrency"] == "app.yaml"
    assert sources["engine.ingest.doc_concurrency"] == "env"
    assert sources["engine.impl"] == "app.yaml"
    assert sources["engine.memory.consolidation_batch_size"] == "default"


def test_get_reports_env_values_as_effective(client, monkeypatch):
    monkeypatch.setenv("TKB_ENGINE_INGEST_DOC_CONCURRENCY", "3")
    c, _ = client
    assert c.get("/api/config").json()["engine"]["ingest"]["doc_concurrency"] == 3


def test_put_writes_the_runtime_layer_and_never_app_yaml(client):
    c, committed = client
    before = committed.read_bytes()

    body = _body(c)
    body["engine"]["ingest"]["chunk_concurrency"] = 12
    res = c.put("/api/config", json=body)

    assert res.status_code == 200
    assert committed.read_bytes() == before
    runtime = committed.with_name("app.runtime.yaml")
    assert runtime.exists()
    assert "chunk_concurrency: 12" in runtime.read_text(encoding="utf-8")


def test_put_value_wins_over_the_environment(client, monkeypatch):
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8")
    c, _ = client
    assert c.get("/api/config").json()["engine"]["ingest"]["chunk_concurrency"] == 8

    body = _body(c)
    body["engine"]["ingest"]["chunk_concurrency"] = 12
    c.put("/api/config", json=body)

    after = c.get("/api/config").json()
    assert after["engine"]["ingest"]["chunk_concurrency"] == 12
    assert after["sources"]["engine.ingest.chunk_concurrency"] == "runtime"


def test_put_rejects_an_invalid_body_without_writing(client):
    c, committed = client
    body = _body(c)
    body["engine"]["ingest"]["chunk_concurrency"] = 0

    assert c.put("/api/config", json=body).status_code == 422
    assert not committed.with_name("app.runtime.yaml").exists()
