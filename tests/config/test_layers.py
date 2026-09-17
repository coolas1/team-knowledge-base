"""三层解析:优先级顺序、缺席层、每键来源。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from config.overrides import as_leaf_map
from config.schema import (
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_FILE,
    SOURCE_RUNTIME,
    AppConfig,
    load_config,
    load_config_with_sources,
    runtime_config_path,
)
from src.engine.components.archive.config import merge_archive_config


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _patch_archive_env(monkeypatch, **overrides):
    """以固定值替换 settings.archive;未列出的旋钮为 None(env 无意见)。"""
    values = {
        "workspace_dir": "ws", "enabled": None, "threshold": None, "delta": None,
        "review_all": None, "poll_seconds": None, "stability_checks": None,
        "max_attempts": None, "top_k": None, "collision_policy": None,
    }
    values.update(overrides)
    monkeypatch.setattr(
        "src.engine.components.archive.config.settings",
        SimpleNamespace(archive=SimpleNamespace(**values)),
    )


# ── 优先级 ────────────────────────────────────────────────────────────


def test_runtime_layer_sits_beside_the_committed_file():
    assert runtime_config_path(Path("config/app.yaml")) == Path(
        "config/app.runtime.yaml"
    )


def test_absent_layers_change_nothing(monkeypatch, tmp_path):
    """无运行时文件、无环境覆盖 -> 恰好等于 schema 默认值。"""
    for name in ("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "TKB_PLUGIN_IMPL"):
        monkeypatch.delenv(name, raising=False)
    cfg = load_config(tmp_path / "app.yaml")
    assert cfg == AppConfig()


def test_committed_file_alone_resolves_as_before(monkeypatch, tmp_path):
    app_yaml = _write(tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 6\n")
    monkeypatch.delenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", raising=False)

    cfg = load_config(app_yaml)
    assert cfg.engine.ingest.chunk_concurrency == 6


def test_environment_overrides_the_committed_default(monkeypatch, tmp_path):
    app_yaml = _write(tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 4\n")
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8")

    cfg = load_config(app_yaml)
    assert cfg.engine.ingest.chunk_concurrency == 8


def test_runtime_edits_override_the_environment(monkeypatch, tmp_path):
    app_yaml = _write(tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 4\n")
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8")
    _write(
        runtime_config_path(app_yaml),
        "engine:\n  ingest:\n    chunk_concurrency: 12\n",
    )

    cfg = load_config(app_yaml)
    assert cfg.engine.ingest.chunk_concurrency == 12


def test_runtime_file_alone_overrides_the_committed_default(monkeypatch, tmp_path):
    app_yaml = _write(tmp_path / "app.yaml", "plugin:\n  impl: tkb\n")
    monkeypatch.delenv("TKB_PLUGIN_IMPL", raising=False)
    _write(runtime_config_path(app_yaml), "plugin:\n  impl: other\n")

    assert load_config(app_yaml).plugin.impl == "other"


def test_env_value_uses_the_schema_type_and_names_the_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "0")
    with pytest.raises(ValidationError) as excinfo:
        load_config(tmp_path / "app.yaml")
    assert "chunk_concurrency" in str(excinfo.value)


def test_env_bool_coercion_matches_a_file_value(monkeypatch, tmp_path):
    monkeypatch.setenv("TKB_ENGINE_MEMORY_ENABLED", "true")
    monkeypatch.setenv("TKB_ENGINE_MEMORY_GRAPH_WORKER", "false")

    cfg = load_config(tmp_path / "app.yaml")
    assert cfg.engine.memory.enabled is True
    assert cfg.engine.memory.graph_worker is False


# ── 环境层:`.env` 与真实环境变量 ─────────────────────────────────────


def test_dotenv_supplies_the_environment_layer(tmp_path):
    """宿主运行与部署共用一份 .env:里面写的派生名就是覆盖。"""
    env_file = _write(
        tmp_path / ".env",
        "# 注释与无关键都不影响\n"
        "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY=16\n"
        "POSTGRES_HOST=elsewhere\n",
    )
    cfg = load_config(tmp_path / "app.yaml", dotenv_path=env_file)
    assert cfg.engine.ingest.chunk_concurrency == 16


def test_process_environment_beats_dotenv(tmp_path, monkeypatch):
    """与 pydantic-settings 一致:真实环境变量 > .env。"""
    env_file = _write(tmp_path / ".env", "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY=16\n")
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "32")

    cfg = load_config(tmp_path / "app.yaml", dotenv_path=env_file)
    assert cfg.engine.ingest.chunk_concurrency == 32


def test_dotenv_overrides_the_committed_default(tmp_path):
    app_yaml = _write(
        tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 4\n"
    )
    env_file = _write(tmp_path / ".env", "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY=16\n")

    assert load_config(app_yaml, dotenv_path=env_file).engine.ingest.chunk_concurrency == 16


def test_missing_dotenv_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", raising=False)
    cfg = load_config(tmp_path / "app.yaml", dotenv_path=tmp_path / "absent.env")
    assert cfg == AppConfig()


def test_dotenv_keys_outside_the_schema_are_ignored(tmp_path):
    env_file = _write(
        tmp_path / ".env",
        "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY=8\nTKB_NOT_A_SCHEMA_PATH=1\n",
    )
    cfg = load_config(tmp_path / "app.yaml", dotenv_path=env_file)
    assert cfg.engine.ingest.chunk_concurrency == 8


def test_dotenv_value_uses_the_schema_type(tmp_path):
    env_file = _write(
        tmp_path / ".env", "TKB_ENGINE_MEMORY_ENABLED=true\nTKB_PLUGIN_IMPL=other\n"
    )
    cfg = load_config(tmp_path / "app.yaml", dotenv_path=env_file)
    assert cfg.engine.memory.enabled is True
    assert cfg.plugin.impl == "other"


def test_dotenv_is_reported_as_the_environment_layer(tmp_path):
    env_file = _write(tmp_path / ".env", "TKB_ENGINE_INGEST_DOC_CONCURRENCY=3\n")
    _, sources = load_config_with_sources(tmp_path / "app.yaml", dotenv_path=env_file)
    assert sources["engine.ingest.doc_concurrency"] == SOURCE_ENV


def test_suite_does_not_read_the_repository_dotenv():
    """守卫:测试进程里 .env 已被隔离,否则断言会随开发机的 .env 漂移。"""
    from config.schema import DOTENV_PATH

    assert not DOTENV_PATH.exists()


# ── 已提交文件不被写 ──────────────────────────────────────────────────


def test_writing_the_runtime_layer_leaves_the_committed_file_alone(
    monkeypatch, tmp_path
):
    text = "engine:\n  ingest:\n    chunk_concurrency: 4\n"
    app_yaml = _write(tmp_path / "app.yaml", text)
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8")
    _write(runtime_config_path(app_yaml), "plugin:\n  impl: other\n")

    load_config(app_yaml)
    assert app_yaml.read_text(encoding="utf-8") == text


# ── 来源报告 ──────────────────────────────────────────────────────────


def test_sources_report_each_layer(monkeypatch, tmp_path):
    app_yaml = _write(
        tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 4\n"
    )
    monkeypatch.setenv("TKB_ENGINE_INGEST_DOC_CONCURRENCY", "3")
    _write(runtime_config_path(app_yaml), "plugin:\n  impl: runtime\n")

    _, sources = load_config_with_sources(app_yaml)
    assert sources["engine.ingest.chunk_concurrency"] == SOURCE_FILE
    assert sources["engine.ingest.doc_concurrency"] == SOURCE_ENV
    assert sources["plugin.impl"] == SOURCE_RUNTIME
    assert sources["engine.impl"] == SOURCE_DEFAULT


def test_sources_report_the_highest_layer_only(monkeypatch, tmp_path):
    app_yaml = _write(
        tmp_path / "app.yaml", "engine:\n  ingest:\n    chunk_concurrency: 4\n"
    )
    monkeypatch.setenv("TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8")
    _write(
        runtime_config_path(app_yaml),
        "engine:\n  ingest:\n    chunk_concurrency: 12\n",
    )

    _, sources = load_config_with_sources(app_yaml)
    assert sources["engine.ingest.chunk_concurrency"] == SOURCE_RUNTIME


def test_sources_cover_every_leaf(monkeypatch, tmp_path):
    cfg, sources = load_config_with_sources(tmp_path / "app.yaml")
    assert set(sources) == set(as_leaf_map(cfg.model_dump()))
    assert set(sources.values()) <= {
        SOURCE_DEFAULT, SOURCE_FILE, SOURCE_ENV, SOURCE_RUNTIME,
    }


# ── 归档三方优先级(派生规则与既有 merge 的组合) ──────────────────────


def test_archive_precedence_three_way(monkeypatch, tmp_path):
    """ARCHIVE_* > TKB_ARCHIVE_* > app.yaml,且不替换既有 merge。"""
    app_yaml = _write(tmp_path / "app.yaml", "archive:\n  threshold: 0.6\n")
    monkeypatch.delenv("ARCHIVE_THRESHOLD", raising=False)
    _patch_archive_env(monkeypatch, threshold=None)

    # 1) 只有 app.yaml
    monkeypatch.delenv("TKB_ARCHIVE_THRESHOLD", raising=False)
    assert merge_archive_config(load_config(app_yaml)).threshold == 0.6

    # 2) 派生名接着覆盖 app.yaml
    monkeypatch.setenv("TKB_ARCHIVE_THRESHOLD", "0.5")
    assert merge_archive_config(load_config(app_yaml)).threshold == 0.5

    # 3) 显式 ARCHIVE_* 最终胜出
    _patch_archive_env(monkeypatch, threshold=0.9)
    assert merge_archive_config(load_config(app_yaml)).threshold == 0.9
