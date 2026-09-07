"""BFF dependencies: wire the engine (inprocess | mcp) and the agent plugin.

The engine is accessed via the agent EngineClient abstraction (dicts), so
inprocess vs mcp is pure wiring. State is held in module-level singletons set
by the app lifespan.
"""

from __future__ import annotations

from pathlib import Path

from src.agent.codex.plugin import build_plugin
from src.agent.engine_client import InProcessEngineClient, McpEngineClient
from src.agent.interface import AgentPlugin, EngineClient, LlmClient
from src.engine.components.store.postgres import init_db
from src.engine.config import EngineConfig, build_engine
from src.engine.mcp import (
    set_conversation_memory_service,
    set_kb,
    set_query_service,
)
from config.schema import AppConfig, load_config
from config.settings import settings

_engine_client: EngineClient | None = None
_plugin: AgentPlugin | None = None
_llm: LlmClient | None = None
_app_config: AppConfig | None = None
_graph_worker_runtime = None
_conversation_worker_runtime = None


def app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        _app_config = load_config("config/app.yaml")
    return _app_config


async def startup() -> None:
    global _engine_client, _plugin, _graph_worker_runtime
    global _conversation_worker_runtime
    cfg = app_config()
    if cfg.webapp.engine_access == "mcp":
        set_query_service(None)
        set_conversation_memory_service(None)
        _engine_client = McpEngineClient("http://localhost:8000/mcp")
    else:
        await init_db()
        query_service = None
        index_hook = None
        if cfg.hindsight.enabled:
            from src.engine.hindsight_components.adapter import (
                build_knowledge_base_adapter,
            )
            from src.engine.hindsight_components.hook import build_retain_hook
            from src.engine.hindsight_components.query import build_query_service

            query_service = build_query_service()
            index_hook = build_retain_hook(
                max_concurrent=cfg.hindsight.retain_max_concurrent
            )
        kb = build_engine(
            EngineConfig(
                impl=cfg.engine.impl,
                config_dir=Path(cfg.engine.config),
                index_hook=index_hook,
            )
        )
        if cfg.hindsight.enabled:
            kb = build_knowledge_base_adapter(kb)
        set_kb(kb)
        set_query_service(query_service)
        if (
            cfg.hindsight.enabled
            and settings.hindsight_conversation_memory_enabled
        ):
            from src.engine.hindsight_components.conversation_service import (
                build_conversation_memory_service,
            )

            conversation_memory_service = build_conversation_memory_service(
                max_recall_results=settings.hindsight_conversation_recall_limit
            )
            set_conversation_memory_service(conversation_memory_service)
        else:
            set_conversation_memory_service(None)
        _engine_client = InProcessEngineClient(kb, query_service=query_service)
    _plugin = build_plugin(cfg)
    global _llm
    from src.agent.llm import build_llm

    _llm = build_llm()
    if (
        cfg.webapp.engine_access == "inprocess"
        and cfg.hindsight.enabled
        and settings.hindsight_graph_worker_enabled
    ):
        from src.engine.hindsight_components.graph_runtime import (
            build_graph_worker_runtime,
        )

        runtime = build_graph_worker_runtime(
            poll_seconds=settings.hindsight_graph_worker_poll_seconds,
            lease_seconds=settings.hindsight_graph_worker_lease_seconds,
            max_attempts=settings.hindsight_graph_worker_max_attempts,
        )
        await runtime.start()
        _graph_worker_runtime = runtime
    if (
        cfg.webapp.engine_access == "inprocess"
        and cfg.hindsight.enabled
        and settings.hindsight_conversation_memory_enabled
    ):
        from src.engine.hindsight_components.conversation_worker import (
            build_conversation_worker_runtime,
        )

        runtime = build_conversation_worker_runtime(
            poll_seconds=settings.hindsight_conversation_worker_poll_seconds,
            max_concurrent=settings.hindsight_conversation_worker_max_concurrent,
            lease_seconds=settings.hindsight_conversation_worker_lease_seconds,
            max_attempts=settings.hindsight_conversation_worker_max_attempts,
            retry_delay_seconds=settings.hindsight_conversation_worker_retry_seconds,
            max_retry_delay_seconds=(
                settings.hindsight_conversation_worker_max_retry_seconds
            ),
            retention_context=settings.hindsight_conversation_retention_context,
        )
        await runtime.start()
        _conversation_worker_runtime = runtime


async def shutdown() -> None:
    global _graph_worker_runtime, _conversation_worker_runtime
    try:
        try:
            if _conversation_worker_runtime is not None:
                await _conversation_worker_runtime.stop()
        finally:
            _conversation_worker_runtime = None
            if _graph_worker_runtime is not None:
                await _graph_worker_runtime.stop()
    finally:
        _graph_worker_runtime = None
        set_conversation_memory_service(None)
        set_query_service(None)


def get_engine() -> EngineClient:
    assert _engine_client is not None, "engine not initialized"
    return _engine_client


def get_plugin() -> AgentPlugin:
    assert _plugin is not None, "plugin not initialized"
    return _plugin


def get_llm() -> LlmClient | None:
    return _llm


def engine_initialized() -> bool:
    """Whether startup created a real engine (false in isolated BFF tests)."""
    return _engine_client is not None
