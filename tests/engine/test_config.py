from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from config.settings import InfraSettings


def test_appconfig_defaults():
    cfg = AppConfig()
    assert cfg.engine.impl == "graphrag"
    assert cfg.engine.config == "config/engine/graphrag"
    assert cfg.agent.harness == "codex"
    assert cfg.agent.skills == ["search_and_answer", "ingest_and_summarize"]
    assert cfg.agent.memory == {"impl": None}
    assert cfg.frontend.impl == "webapp"
    assert cfg.webapp.engine_access == "inprocess"


def test_load_config_reads_app_yaml(tmp_path: Path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        "engine:\n  impl: graphrag\n  config: config/engine/graphrag\n"
        "agent:\n  harness: codex\n  skills: [search_and_answer]\n"
        "  memory: {impl: null}\nfrontend:\n  impl: webapp\n"
        "webapp:\n  engine_access: mcp\n"
    )
    cfg = load_config(app_yaml)
    assert cfg.webapp.engine_access == "mcp"
    assert cfg.agent.skills == ["search_and_answer"]


def test_load_config_missing_file_uses_defaults(tmp_path: Path):
    cfg = load_config(tmp_path / "does_not_exist.yaml")
    assert cfg.engine.impl == "graphrag"


def test_infra_settings_postgres_dsn():
    s = InfraSettings(
        postgres_user="u", postgres_password="p",
        postgres_host="h", postgres_port=5433, postgres_db="d",
    )
    assert s.postgres_dsn == "postgresql+asyncpg://u:p@h:5433/d"
