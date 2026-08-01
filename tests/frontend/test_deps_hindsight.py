import pytest

from config.schema import AppConfig
from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.frontend.webapp.server import deps
from tests.conftest import FakeKnowledgeBase


@pytest.fixture(autouse=True)
def reset_deps_state():
    deps._engine_client = None
    deps._plugin = None
    deps._llm = None
    yield
    deps._engine_client = None
    deps._plugin = None
    deps._llm = None


async def test_startup_wires_hindsight_for_inprocess(monkeypatch):
    kb = FakeKnowledgeBase()
    query_service = object()
    retain_hook = object()
    captured = {}

    async def init_db():
        pass

    def build_engine(config):
        captured["engine_config"] = config
        return kb

    monkeypatch.setattr(
        deps,
        "app_config",
        lambda: AppConfig.model_validate(
            {
                "hindsight": {"enabled": True, "retain_max_concurrent": 3},
                "webapp": {"engine_access": "inprocess"},
            }
        ),
    )
    monkeypatch.setattr(deps, "init_db", init_db)
    monkeypatch.setattr(deps, "build_engine", build_engine)
    monkeypatch.setattr(deps, "build_plugin", lambda config: object())
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: None)
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.build_query_service",
        lambda: query_service,
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.hook.build_retain_hook",
        lambda *, max_concurrent: (
            captured.update(max_concurrent=max_concurrent) or retain_hook
        ),
    )
    monkeypatch.setattr(
        deps, "set_query_service", lambda service: captured.update(mcp_query=service)
    )
    monkeypatch.setattr(deps, "set_kb", lambda value: None)

    await deps.startup()

    assert isinstance(deps.get_engine(), InProcessEngineClient)
    assert deps.get_engine()._query_service is query_service
    assert captured["engine_config"].index_hook is retain_hook
    assert captured["max_concurrent"] == 3
    assert captured["mcp_query"] is query_service


async def test_startup_keeps_hindsight_disabled_by_default(monkeypatch):
    kb = FakeKnowledgeBase()
    captured = {}

    async def init_db():
        pass

    def build_engine(config):
        captured["engine_config"] = config
        return kb

    monkeypatch.setattr(deps, "app_config", AppConfig)
    monkeypatch.setattr(deps, "init_db", init_db)
    monkeypatch.setattr(deps, "build_engine", build_engine)
    monkeypatch.setattr(deps, "build_plugin", lambda config: object())
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: None)
    monkeypatch.setattr(deps, "set_kb", lambda value: None)
    monkeypatch.setattr(
        deps, "set_query_service", lambda service: captured.update(query=service)
    )

    await deps.startup()

    assert captured["engine_config"].index_hook is None
    assert deps.get_engine()._query_service is None
    assert captured["query"] is None


async def test_mcp_access_does_not_build_local_hindsight(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        deps,
        "app_config",
        lambda: AppConfig.model_validate(
            {
                "hindsight": {"enabled": True},
                "webapp": {"engine_access": "mcp"},
            }
        ),
    )
    monkeypatch.setattr(deps, "build_plugin", lambda config: object())
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: None)
    monkeypatch.setattr(
        deps, "set_query_service", lambda service: captured.update(query=service)
    )

    await deps.startup()

    assert isinstance(deps.get_engine(), McpEngineClient)
    assert captured["query"] is None
