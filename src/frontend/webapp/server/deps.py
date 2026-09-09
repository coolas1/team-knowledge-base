"""BFF dependencies: single in-process wiring (engine + plugin + MCP).

memory flags come from AppConfig.engine.memory; everything runs in one
process. No HTTP fallbacks: the engine/plugin containers are gone."""

from __future__ import annotations

import os
from fastapi import Request, HTTPException
from src.engine.trusted_scope import bind_service, resolve_binding
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
_consolidation_worker = None
_mental_model_worker = None
_app_config: AppConfig | None = None


def app_config() -> AppConfig:
    global _app_config
    if _app_config is None:
        _app_config = load_config(os.getenv("APP_CONFIG", "config/app.yaml"))
    return _app_config


async def startup() -> None:
    global _kb, _plugin, _llm, _query
    global _graph_worker, _conversation_worker, _consolidation_worker
    global _mental_model_worker
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
        from src.engine.hindsight_components.graph_runtime import (
            build_graph_worker_runtime,
        )

        _graph_worker = build_graph_worker_runtime(
            poll_seconds=settings.hindsight_graph_worker_poll_seconds,
            lease_seconds=settings.hindsight_graph_worker_lease_seconds,
            max_attempts=settings.hindsight_graph_worker_max_attempts,
        )
        await _graph_worker.start()

    if (
        cfg.engine.memory.enabled
        and cfg.engine.memory.features.consolidation
        and cfg.engine.memory.consolidation_worker
    ):
        from src.engine.hindsight_components.consolidation_runtime import (
            build_consolidation_worker_runtime,
        )

        _consolidation_worker = build_consolidation_worker_runtime(
            max_concurrent=cfg.engine.memory.consolidation_max_concurrent,
            batch_size=cfg.engine.memory.consolidation_batch_size,
            observation_limit=cfg.engine.memory.consolidation_observation_limit,
            max_iterations=cfg.engine.memory.consolidation_max_iterations,
            max_tokens=cfg.engine.memory.consolidation_max_tokens,
            llm_timeout_seconds=(cfg.engine.memory.consolidation_llm_timeout_seconds),
            lease_seconds=cfg.engine.memory.consolidation_lease_seconds,
            max_output_tokens=cfg.engine.memory.consolidation_max_output_tokens,
            max_cost_usd=cfg.engine.memory.consolidation_max_cost_usd,
            input_cost_usd_per_million=(
                cfg.engine.memory.consolidation_input_cost_usd_per_million
            ),
            output_cost_usd_per_million=(
                cfg.engine.memory.consolidation_output_cost_usd_per_million
            ),
            semantic_dedup_enabled=cfg.engine.memory.consolidation_semantic_dedup,
            semantic_threshold=cfg.engine.memory.consolidation_semantic_threshold,
        )
        await _consolidation_worker.start()

    if (
        cfg.engine.memory.enabled
        and cfg.engine.memory.features.mental_models
        and cfg.engine.memory.mental_model_worker
    ):
        from src.engine.hindsight_components.mental_model_runtime import (
            build_mental_model_worker_runtime,
        )

        _mental_model_worker = build_mental_model_worker_runtime(
            poll_seconds=cfg.engine.memory.mental_model_poll_seconds,
            max_concurrent=cfg.engine.memory.mental_model_max_concurrent,
            recall_results=cfg.engine.memory.mental_model_recall_results,
            max_evidence_tokens=cfg.engine.memory.mental_model_max_evidence_tokens,
            max_output_tokens=cfg.engine.memory.mental_model_max_output_tokens,
            lease_seconds=cfg.engine.memory.mental_model_lease_seconds,
            max_attempts=cfg.engine.memory.mental_model_max_attempts,
            input_cost_usd_per_million=(
                cfg.engine.memory.mental_model_input_cost_usd_per_million
            ),
            output_cost_usd_per_million=(
                cfg.engine.memory.mental_model_output_cost_usd_per_million
            ),
            use_adaptive_reflect=cfg.engine.memory.mental_model_use_adaptive_reflect,
        )
        await _mental_model_worker.start()

    if cfg.engine.memory.enabled and settings.hindsight_conversation_memory_enabled:
        from src.engine.hindsight_components.conversation_service import (
            build_conversation_memory_service,
        )
        from src.engine.hindsight_components.conversation_worker import (
            build_conversation_worker_runtime,
        )

        set_conversation_memory_service(
            build_conversation_memory_service(
                max_recall_results=settings.hindsight_conversation_recall_limit,
                consolidation_enabled=cfg.engine.memory.features.consolidation,
            )
        )
        _conversation_worker = build_conversation_worker_runtime(
            poll_seconds=settings.hindsight_conversation_worker_poll_seconds,
            max_concurrent=(settings.hindsight_conversation_worker_max_concurrent),
            lease_seconds=settings.hindsight_conversation_worker_lease_seconds,
            max_attempts=settings.hindsight_conversation_worker_max_attempts,
            retry_delay_seconds=(settings.hindsight_conversation_worker_retry_seconds),
            max_retry_delay_seconds=(
                settings.hindsight_conversation_worker_max_retry_seconds
            ),
            retention_context=(settings.hindsight_conversation_retention_context),
            consolidation_enabled=cfg.engine.memory.features.consolidation,
        )
        await _conversation_worker.start()
    else:
        set_conversation_memory_service(None)


async def shutdown() -> None:
    global _graph_worker, _conversation_worker, _consolidation_worker
    global _mental_model_worker, _query
    try:
        try:
            if _conversation_worker is not None:
                await _conversation_worker.stop()
        finally:
            _conversation_worker = None
            try:
                try:
                    if _mental_model_worker is not None:
                        await _mental_model_worker.stop()
                finally:
                    _mental_model_worker = None
                    if _consolidation_worker is not None:
                        await _consolidation_worker.stop()
            finally:
                _consolidation_worker = None
                if _graph_worker is not None:
                    await _graph_worker.stop()
    finally:
        _graph_worker = None
        from src.agent.tkb.mcp.server import set_conversation_memory_service

        set_conversation_memory_service(None)
        _query = None


def _binding(request: Request | None):
    try:
        return resolve_binding(
            request.headers if request is not None else {},
            enabled=app_config().engine.memory.features.scope,
            bindings=settings.memory_scope_bindings,
        )
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error


def get_kb(request: Request = None) -> KnowledgeBase:
    assert _kb is not None, "engine not initialized"
    return bind_service(_kb, _binding(request), writes=True)


def get_plugin() -> LoadedPlugin:
    assert _plugin is not None, "plugin not initialized"
    return _plugin


def get_llm():
    return _llm


def get_query(request: Request = None) -> KnowledgeQuery | None:
    return bind_service(_query, _binding(request))


def engine_initialized() -> bool:
    return _kb is not None
