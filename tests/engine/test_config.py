from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from config.settings import InfraSettings


def test_appconfig_defaults():
    cfg = AppConfig()
    assert cfg.engine.impl == "graphrag"
    assert cfg.engine.config == "config/engine/graphrag"
    assert cfg.plugin.impl == "tkb"
    assert cfg.engine.memory.enabled is False
    assert cfg.engine.memory.retain_max_concurrent == 1
    assert cfg.engine.memory.retain_chunk_concurrency == 4


def test_load_config_reads_app_yaml(tmp_path: Path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        "engine:\n  impl: graphrag\n  config: config/engine/graphrag\n"
        "  memory:\n    enabled: true\n    retain_max_concurrent: 2\n"
        "plugin:\n  impl: tkb\n"
    )
    cfg = load_config(app_yaml)
    assert cfg.plugin.impl == "tkb"
    assert cfg.engine.memory.enabled is True
    assert cfg.engine.memory.retain_max_concurrent == 2


def test_memory_retain_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {"engine": {"memory": {"enabled": True, "retain_max_concurrent": 0}}}
        )


def test_load_config_missing_file_uses_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does_not_exist.yaml")
    assert cfg.engine.impl == "graphrag"


def test_infra_settings_postgres_dsn():
    s = InfraSettings(
        _env_file=None,
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=5433,
        postgres_db="d",
    )
    assert s.postgres_dsn == "postgresql+asyncpg://u:p@h:5433/d"
    assert s.hindsight_graph_worker_enabled is True
    assert s.hindsight_graph_worker_poll_seconds == 1.0
    assert s.hindsight_graph_worker_lease_seconds == 300
    assert s.hindsight_graph_worker_max_attempts == 10
    assert s.hindsight_conversation_memory_enabled is False
    assert s.hindsight_conversation_recall_limit == 20
    assert s.hindsight_conversation_worker_poll_seconds == 1.0
    assert s.hindsight_conversation_worker_lease_seconds == 300
    assert s.hindsight_conversation_worker_max_attempts == 10
    assert s.hindsight_conversation_worker_max_concurrent == 1
    assert s.hindsight_conversation_worker_retry_seconds == 1.0
    assert s.hindsight_conversation_worker_max_retry_seconds == 300.0


def test_graph_worker_settings_must_be_positive():
    with pytest.raises(ValueError):
        InfraSettings(hindsight_graph_worker_poll_seconds=0)
    with pytest.raises(ValueError):
        InfraSettings(hindsight_graph_worker_lease_seconds=0)
    with pytest.raises(ValueError):
        InfraSettings(hindsight_graph_worker_max_attempts=0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hindsight_conversation_recall_limit", 0),
        ("hindsight_conversation_worker_poll_seconds", 0),
        ("hindsight_conversation_worker_lease_seconds", 0),
        ("hindsight_conversation_worker_max_attempts", 0),
        ("hindsight_conversation_worker_max_concurrent", 0),
        ("hindsight_conversation_worker_retry_seconds", 0),
        ("hindsight_conversation_worker_max_retry_seconds", 0),
        ("hindsight_conversation_retention_context", ""),
    ],
)
def test_conversation_memory_settings_reject_invalid_limits(field, value):
    with pytest.raises(ValueError):
        InfraSettings(_env_file=None, **{field: value})


def test_appconfig_ingest_defaults():
    cfg = AppConfig()
    assert cfg.engine.ingest.chunk_concurrency == 4
    assert cfg.engine.ingest.doc_concurrency == 2
    assert cfg.engine.ingest.llm_retries == 3
    assert cfg.engine.ingest.llm_backoff_base_seconds == 2.0


def test_ingest_concurrency_must_be_positive():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"engine": {"ingest": {"chunk_concurrency": 0}}})
    with pytest.raises(ValueError):
        AppConfig.model_validate({"engine": {"ingest": {"doc_concurrency": 0}}})


def test_engine_config_maps_ingest_concurrency():
    from src.engine.config import engine_config_from_app

    app = AppConfig.model_validate(
        {
            "engine": {
                "ingest": {
                    "chunk_concurrency": 8,
                    "doc_concurrency": 3,
                    "llm_retries": 5,
                    "llm_backoff_base_seconds": 0.5,
                }
            }
        }
    )
    ecfg = engine_config_from_app(app)
    assert ecfg.ingest.chunk_concurrency == 8
    assert ecfg.ingest.doc_concurrency == 3
    assert ecfg.ingest.llm_retries == 5
    assert ecfg.ingest.llm_backoff_base_seconds == 0.5


def test_engine_config_ingest_defaults_when_absent():
    from src.engine.config import EngineConfig, IngestSettings

    ecfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    assert ecfg.ingest == IngestSettings()
