"""deps.startup(): the single wiring path, memory on and off."""
from pathlib import Path

import pytest

from src.frontend.webapp.server import deps


class FakeKB:
    capabilities = None


@pytest.fixture(autouse=True)
def _reset():
    deps._kb = (
        deps._plugin
    ) = (
        deps._llm
    ) = deps._query = deps._graph_worker = deps._conversation_worker = None
    deps._app_config = None
    yield
    deps._kb = (
        deps._plugin
    ) = (
        deps._llm
    ) = deps._query = deps._graph_worker = deps._conversation_worker = None
    deps._app_config = None


@pytest.fixture
def stubs(monkeypatch, tmp_path):
    """Patch the SOURCE modules - startup() does lazy `from X import y`
    at call time, so patching deps-module attributes would be ignored."""

    made = {}
    # startup() mutates the MCP singleton; restore hooks after this test so
    # selecting frontend tests before agent tests does not leak FakePlugin.
    from src.agent.tkb.mcp import server as mcp_server
    monkeypatch.setattr(mcp_server, "_hooks", mcp_server._hooks)

    async def fake_init_db():
        made["init_db"] = True

    async def fake_reconcile():
        made["reconcile"] = True
        return 0

    monkeypatch.setattr(
        "src.engine.graphrag.backend.reconcile_interrupted_processing", fake_reconcile
    )

    def fake_build_engine(ecfg):
        made["engine_config"] = ecfg
        kb = FakeKB()
        kb.ecfg = ecfg
        return kb

    class FakePlugin:
        manifest = type("M", (), {"name": "tkb"})()
        skills = {}
        hooks = object()

    class FakeQuery:
        async def query(self, r):
            raise AssertionError("not called")

    class FakeWorker:
        async def start(self):
            made["worker"] = True

        async def stop(self):
            pass

    monkeypatch.setattr(
        "src.engine.components.store.postgres.init_db", fake_init_db
    )
    # build_engine is bound into deps' namespace at import (top-level import),
    # so patch it there rather than at the source module.
    monkeypatch.setattr("src.frontend.webapp.server.deps.build_engine", fake_build_engine)
    monkeypatch.setattr(
        "src.agent.loader.PluginLoader",
        lambda: type("L", (), {"load": staticmethod(lambda p: FakePlugin())})(),
    )
    monkeypatch.setattr("src.agent.llm.build_llm", lambda: object())
    monkeypatch.setattr(
        "src.engine.hindsight_components.query.build_query_service",
        lambda **kw: FakeQuery(),
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.graph_runtime.build_graph_worker_runtime",
        lambda **kw: FakeWorker(),
    )
    monkeypatch.setenv("APP_CONFIG", str(tmp_path / "app.yaml"))
    return made


def _write_cfg(tmp_path, memory: bool) -> Path:
    p = tmp_path / "app.yaml"
    p.write_text(
        "engine:\n  impl: graphrag\n"
        f"  memory:\n    enabled: {str(memory).lower()}\n"
        "plugin:\n  impl: tkb\n",
        encoding="utf-8",
    )
    return p


@pytest.mark.asyncio
async def test_startup_memory_off(stubs, tmp_path):
    _write_cfg(tmp_path, memory=False)
    import src.agent.tkb.mcp.server as mcp_server

    mcp_server.set_query_service(None)
    await deps.startup()
    assert deps.engine_initialized()
    assert deps.get_query() is None
    assert deps._graph_worker is None
    assert stubs["engine_config"].memory is None


@pytest.mark.asyncio
async def test_startup_reconciles_interrupted_processing(stubs, tmp_path, monkeypatch):
    """启动时对账一次中断的 pipeline，避免文档对检索永久不可见。"""
    _write_cfg(tmp_path, memory=False)
    called = {}

    async def fake_reconcile():
        called["reconcile"] = True
        return 0

    monkeypatch.setattr(
        "src.engine.graphrag.backend.reconcile_interrupted_processing", fake_reconcile
    )
    await deps.startup()

    assert called.get("reconcile") is True


@pytest.mark.asyncio
async def test_startup_memory_on(stubs, tmp_path):
    _write_cfg(tmp_path, memory=True)
    await deps.startup()
    assert deps.get_query() is not None
    assert stubs["engine_config"].memory is not None
    assert stubs["worker"] is True  # graph_worker defaults on with memory


@pytest.mark.asyncio
async def test_startup_conversation_memory_on(stubs, tmp_path, monkeypatch):
    import src.agent.tkb.mcp.server as mcp_server

    _write_cfg(tmp_path, memory=True)
    # settings is a singleton imported at module load; patch the attribute.
    monkeypatch.setattr(deps.settings, "hindsight_conversation_memory_enabled", True)

    started = {}

    class FakeConversationWorker:
        async def start(self):
            started["conversation"] = True

        async def stop(self):
            started["stopped"] = True

    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_service.build_conversation_memory_service",
        lambda **kw: object(),
    )
    monkeypatch.setattr(
        "src.engine.hindsight_components.conversation_worker.build_conversation_worker_runtime",
        lambda **kw: FakeConversationWorker(),
    )

    await deps.startup()
    assert started.get("conversation") is True
    assert deps._conversation_worker is not None
    assert mcp_server._conversation_memory_service is not None

    await deps.shutdown()
    assert started.get("stopped") is True
    assert deps._conversation_worker is None
    assert mcp_server._conversation_memory_service is None


@pytest.mark.asyncio
async def test_startup_conversation_memory_off(stubs, tmp_path, monkeypatch):
    import src.agent.tkb.mcp.server as mcp_server

    _write_cfg(tmp_path, memory=True)
    monkeypatch.setattr(deps.settings, "hindsight_conversation_memory_enabled", False)

    mcp_server.set_conversation_memory_service(object())  # stale state
    await deps.startup()
    assert deps._conversation_worker is None
    assert mcp_server._conversation_memory_service is None


def _write_cfg_with_archive(tmp_path, memory: bool, archive_enabled: bool) -> Path:
    p = tmp_path / "app.yaml"
    p.write_text(
        "engine:\n  impl: graphrag\n"
        f"  memory:\n    enabled: {str(memory).lower()}\n"
        "plugin:\n  impl: tkb\n"
        f"archive:\n  enabled: {str(archive_enabled).lower()}\n",
        encoding="utf-8",
    )
    return p


@pytest.mark.asyncio
async def test_startup_archive_off_leaves_runtime_none(stubs, tmp_path):
    _write_cfg_with_archive(tmp_path, memory=False, archive_enabled=False)
    await deps.startup()
    assert deps.archive_enabled() is False


@pytest.mark.asyncio
async def test_startup_archive_on_builds_and_starts_runtime(stubs, tmp_path, monkeypatch):
    _write_cfg_with_archive(tmp_path, memory=False, archive_enabled=True)
    started = {}

    class FakeArchiveRuntime:
        def __init__(self, config, kb):
            self.config = config
            self.kb = kb

        async def start(self):
            started["runtime"] = True

        async def stop(self):
            started["stopped"] = True

    monkeypatch.setattr(
        "src.engine.components.archive.runtime.build_archive_runtime",
        lambda config, kb: FakeArchiveRuntime(config, kb),
    )

    await deps.startup()
    assert deps.archive_enabled() is True
    assert started["runtime"] is True
    runtime = deps.get_archive()
    assert runtime.kb is deps.get_kb()  # 与 BFF 共享同一个引擎实例
    assert runtime.config.enabled is True

    await deps.shutdown()
    assert started["stopped"] is True
    assert deps._archive_runtime is None
