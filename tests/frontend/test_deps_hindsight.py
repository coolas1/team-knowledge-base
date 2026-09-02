import pytest

from config.schema import AppConfig
from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.engine.hindsight_components.adapter import HindsightKnowledgeBaseAdapter
from src.frontend.webapp.server import deps
from tests.conftest import FakeKnowledgeBase


@pytest.fixture(autouse=True)
def reset_deps_state(monkeypatch):
    deps._engine_client = None
    deps._plugin = None
    deps._llm = None
    deps._graph_worker_runtime = None
    deps._conversation_worker_runtime = None
    monkeypatch.setattr(deps.settings, "hindsight_graph_worker_enabled", False)
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_memory_enabled", False
    )
    yield
    deps._engine_client = None
    deps._plugin = None
    deps._llm = None
    deps._graph_worker_runtime = None
    deps._conversation_worker_runtime = None
    deps.set_conversation_memory_service(None)


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
    assert isinstance(deps.get_engine()._kb, HindsightKnowledgeBaseAdapter)
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


async def test_startup_and_shutdown_manage_opt_in_graph_worker(monkeypatch):
    class Runtime:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

    runtime = Runtime()

    async def init_db():
        pass

    monkeypatch.setattr(
        deps,
        "app_config",
        lambda: AppConfig.model_validate(
            {
                "hindsight": {"enabled": True},
                "webapp": {"engine_access": "inprocess"},
            }
        ),
    )
    monkeypatch.setattr(deps, "init_db", init_db)
    monkeypatch.setattr(deps, "build_engine", lambda config: FakeKnowledgeBase())
    monkeypatch.setattr(deps, "build_plugin", lambda config: object())
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: None)
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.build_query_service",
        lambda: object(),
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.hook.build_retain_hook",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(deps, "set_kb", lambda value: None)
    monkeypatch.setattr(deps, "set_query_service", lambda value: None)
    monkeypatch.setattr(deps.settings, "hindsight_graph_worker_enabled", True)
    monkeypatch.setattr(deps.settings, "hindsight_graph_worker_poll_seconds", 2.0)
    monkeypatch.setattr(deps.settings, "hindsight_graph_worker_lease_seconds", 60)
    monkeypatch.setattr(deps.settings, "hindsight_graph_worker_max_attempts", 4)

    captured = {}

    def build_runtime(**kwargs):
        captured.update(kwargs)
        return runtime

    monkeypatch.setattr(
        "src.engine.hindsight_components.graph_runtime.build_graph_worker_runtime",
        build_runtime,
    )

    await deps.startup()
    assert runtime.started == 1
    assert captured == {
        "poll_seconds": 2.0,
        "lease_seconds": 60,
        "max_attempts": 4,
    }

    await deps.shutdown()
    assert runtime.stopped == 1
    assert deps._graph_worker_runtime is None


async def test_startup_and_shutdown_manage_conversation_memory_runtime(monkeypatch):
    class Runtime:
        def __init__(self):
            self.started = 0
            self.stopped = 0

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

    runtime = Runtime()
    conversation_service = object()
    captured = {}

    async def init_db():
        pass

    monkeypatch.setattr(
        deps,
        "app_config",
        lambda: AppConfig.model_validate(
            {
                "hindsight": {"enabled": True},
                "webapp": {"engine_access": "inprocess"},
            }
        ),
    )
    monkeypatch.setattr(deps, "init_db", init_db)
    monkeypatch.setattr(deps, "build_engine", lambda config: FakeKnowledgeBase())
    monkeypatch.setattr(deps, "build_plugin", lambda config: object())
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: None)
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.build_query_service", lambda: object()
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.hook.build_retain_hook", lambda **kwargs: object()
    )
    monkeypatch.setattr(deps, "set_kb", lambda value: None)
    monkeypatch.setattr(deps, "set_query_service", lambda value: None)
    monkeypatch.setattr(
        deps,
        "set_conversation_memory_service",
        lambda value: captured.update(service=value),
    )
    monkeypatch.setattr(deps.settings, "hindsight_conversation_memory_enabled", True)
    monkeypatch.setattr(deps.settings, "hindsight_conversation_recall_limit", 7)
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_poll_seconds", 2.0
    )
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_lease_seconds", 60
    )
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_max_attempts", 4
    )
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_max_concurrent", 3
    )
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_retry_seconds", 2.0
    )
    monkeypatch.setattr(
        deps.settings, "hindsight_conversation_worker_max_retry_seconds", 30.0
    )
    monkeypatch.setattr(
        deps.settings,
        "hindsight_conversation_retention_context",
        "Team chat turn",
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_service.build_conversation_memory_service",
        lambda **kwargs: (
            captured.update(service_build=kwargs) or conversation_service
        ),
    )

    def build_runtime(**kwargs):
        captured["runtime_build"] = kwargs
        return runtime

    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_worker.build_conversation_worker_runtime",
        build_runtime,
    )

    await deps.startup()

    assert runtime.started == 1
    assert captured["service"] is conversation_service
    assert captured["service_build"] == {"max_recall_results": 7}
    assert captured["runtime_build"] == {
        "poll_seconds": 2.0,
        "max_concurrent": 3,
        "lease_seconds": 60,
        "max_attempts": 4,
        "retry_delay_seconds": 2.0,
        "max_retry_delay_seconds": 30.0,
        "retention_context": "Team chat turn",
    }

    await deps.shutdown()
    assert runtime.stopped == 1
    assert deps._conversation_worker_runtime is None
    assert captured["service"] is None
