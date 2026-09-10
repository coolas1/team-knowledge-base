"""BFF dependencies: single in-process wiring (engine + plugin + MCP).

memory flags come from AppConfig.engine.memory; everything runs in one
process. No HTTP fallbacks: the engine/plugin containers are gone."""
from __future__ import annotations

import os
from pathlib import Path

from src.engine.config import build_engine, engine_config_from_app
from src.engine.interface import KnowledgeBase, KnowledgeQuery
from src.agent.interface import LoadedPlugin
from config.schema import AppConfig, load_config
from config.settings import settings

_kb: KnowledgeBase | None = None
_plugin: LoadedPlugin | None = None
_llm = None
_query: KnowledgeQuery | None = None
_graph_worker = None
_conversation_worker = None
_app_config: AppConfig | None = None


def app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        _app_config = load_config(os.getenv("APP_CONFIG", "config/app.yaml"))
    return _app_config


async def startup() -> None:
    global _kb, _plugin, _llm, _query, _graph_worker, _conversation_worker
    cfg = app_config()

    from src.engine.components.store.postgres import init_db

    await init_db()
    _kb = build_engine(engine_config_from_app(cfg))

    if cfg.engine.memory.enabled:
        from src.engine.hindsight_components.query import build_query_service

        _query = build_query_service()

    from src.agent.loader import PluginLoader

    plugin_path = Path("src/agent") / cfg.plugin.impl
    _plugin = PluginLoader().load(plugin_path)

    from src.agent.llm import build_llm

    _llm = build_llm()

    from src.agent.tkb.mcp.server import (
        set_conversation_memory_service,
        set_hooks,
        set_kb,
        set_query_service,
    )

    set_kb(_kb)
    set_query_service(_query)
    set_hooks(_plugin.hooks)

    if (
        cfg.engine.memory.enabled
        and cfg.engine.memory.graph_worker
        and settings.hindsight_graph_worker_enabled
    ):
        from src.engine.hindsight_components.graph_runtime import build_graph_worker_runtime

        _graph_worker = build_graph_worker_runtime(
            poll_seconds=settings.hindsight_graph_worker_poll_seconds,
            lease_seconds=settings.hindsight_graph_worker_lease_seconds,
            max_attempts=settings.hindsight_graph_worker_max_attempts,
        )
        await _graph_worker.start()

    if (
        cfg.engine.memory.enabled
        and settings.hindsight_conversation_memory_enabled
    ):
        from src.engine.hindsight_components.conversation_service import (
            build_conversation_memory_service,
        )
        from src.engine.hindsight_components.conversation_worker import (
            build_conversation_worker_runtime,
        )

        set_conversation_memory_service(
            build_conversation_memory_service(
                max_recall_results=settings.hindsight_conversation_recall_limit,
                max_turn_chars=settings.hindsight_conversation_max_turn_chars,
            )
        )
        _conversation_worker = build_conversation_worker_runtime(
            poll_seconds=settings.hindsight_conversation_worker_poll_seconds,
            max_concurrent=(
                settings.hindsight_conversation_worker_max_concurrent
            ),
            lease_seconds=settings.hindsight_conversation_worker_lease_seconds,
            max_attempts=settings.hindsight_conversation_worker_max_attempts,
            retry_delay_seconds=(
                settings.hindsight_conversation_worker_retry_seconds
            ),
            max_retry_delay_seconds=(
                settings.hindsight_conversation_worker_max_retry_seconds
            ),
            retention_context=(
                settings.hindsight_conversation_retention_context
            ),
        )
        await _conversation_worker.start()
    else:
        set_conversation_memory_service(None)


async def shutdown() -> None:
    global _graph_worker, _conversation_worker, _query
    try:
        try:
            if _conversation_worker is not None:
                await _conversation_worker.stop()
        finally:
            _conversation_worker = None
            if _graph_worker is not None:
                await _graph_worker.stop()
    finally:
        _graph_worker = None
        from src.agent.tkb.mcp.server import set_conversation_memory_service

        set_conversation_memory_service(None)
        _query = None


def get_kb() -> KnowledgeBase:
    assert _kb is not None, "engine not initialized"
    return _kb


def get_plugin() -> LoadedPlugin:
    assert _plugin is not None, "plugin not initialized"
    return _plugin


def get_llm():
    return _llm


def get_query() -> KnowledgeQuery | None:
    return _query


def engine_initialized() -> bool:
    return _kb is not None
