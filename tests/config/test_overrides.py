"""派生覆盖名:命名规则、冲突守卫、ARCHIVE_* 别名。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from config.overrides import (
    ARCHIVE_ENV_ALIASES,
    assert_no_collisions,
    derive_env_name,
    derived_names,
    env_overrides,
    find_collisions,
    unused_archive_aliases,
)
from config.schema import load_config
from src.engine.components.archive.config import merge_archive_config


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


# ── 命名规则 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("engine.ingest.chunk_concurrency", "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY"),
        ("engine.memory.consolidation_batch_size",
         "TKB_ENGINE_MEMORY_CONSOLIDATION_BATCH_SIZE"),
        ("engine.memory.features.scope", "TKB_ENGINE_MEMORY_FEATURES_SCOPE"),
        ("plugin.impl", "TKB_PLUGIN_IMPL"),
        ("archive.collision_policy", "TKB_ARCHIVE_COLLISION_POLICY"),
    ],
)
def test_derive_env_name_from_path(path, expected):
    assert derive_env_name(path) == expected


def test_derived_names_cover_every_schema_leaf():
    names = derived_names()
    # 覆盖面不是估算:每个叶子都在,且名字都是 TKB_ 前缀的大写。
    assert "engine.ingest.chunk_concurrency" in names
    assert "engine.memory.consolidation_batch_size" in names
    assert "engine.memory.features.adaptive_reflect" in names
    assert "archive.enabled" in names
    assert all(name.startswith("TKB_") for name in names.values())
    assert all(name == name.upper() for name in names.values())


def test_underscores_in_key_names_do_not_create_ambiguity():
    """名字只生成不反解析,所以 `chunk_concurrency` 的下划线无害。"""
    names = derived_names()
    derived = {name: path for path, name in names.items()}
    assert derived["TKB_ENGINE_INGEST_CHUNK_CONCURRENCY"] == (
        "engine.ingest.chunk_concurrency"
    )


def test_env_overrides_reads_only_derived_names():
    found = env_overrides(
        {
            "TKB_ENGINE_INGEST_DOC_CONCURRENCY": "3",
            "TKB_NOT_A_SCHEMA_PATH": "1",
            "ENGINE_INGEST_DOC_CONCURRENCY": "9",
        }
    )
    assert found == {"engine.ingest.doc_concurrency": "3"}


# ── 冲突守卫 ──────────────────────────────────────────────────────────


class _Inner(BaseModel):
    c: int = 1


class _Colliding(BaseModel):
    """`ab.c` 与 `ab_c` 派生出同一个名字。"""

    ab: _Inner = Field(default_factory=_Inner)
    ab_c: int = 1


def test_real_schema_has_no_collisions():
    assert find_collisions() == {}
    assert_no_collisions()


def test_collision_is_detected_and_names_both_paths():
    collisions = find_collisions(derived_names(_Colliding))
    assert collisions == {"TKB_AB_C": ["ab.c", "ab_c"]}

    with pytest.raises(ValueError) as excinfo:
        assert_no_collisions(derived_names(_Colliding))
    assert "ab.c" in str(excinfo.value)
    assert "ab_c" in str(excinfo.value)
    assert "TKB_AB_C" in str(excinfo.value)


# ── ARCHIVE_* 别名 ────────────────────────────────────────────────────


def test_alias_table_points_at_real_archive_keys():
    assert ARCHIVE_ENV_ALIASES["archive.enabled"] == "ARCHIVE_ENABLED"
    assert unused_archive_aliases() == {}


def test_alias_archive_env_beats_derived_equivalent(monkeypatch, tmp_path):
    """三方优先级:ARCHIVE_* > TKB_ARCHIVE_* > app.yaml。"""
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("archive:\n  threshold: 0.6\n", encoding="utf-8")
    monkeypatch.setenv("TKB_ARCHIVE_THRESHOLD", "0.5")
    monkeypatch.setenv("ARCHIVE_THRESHOLD", "0.9")
    _patch_archive_env(monkeypatch, threshold=0.9)

    app = load_config(app_yaml)
    assert app.archive.threshold == 0.5  # 派生名已写入 app 配置层
    assert merge_archive_config(app).threshold == 0.9  # 显式别名最终胜出


def test_alias_derived_name_reaches_the_archive_key(monkeypatch, tmp_path):
    """别名未设置时,派生名接着生效。"""
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("archive:\n  threshold: 0.6\n", encoding="utf-8")
    monkeypatch.setenv("TKB_ARCHIVE_THRESHOLD", "0.5")
    monkeypatch.delenv("ARCHIVE_THRESHOLD", raising=False)
    _patch_archive_env(monkeypatch, threshold=None)

    app = load_config(app_yaml)
    assert merge_archive_config(app).threshold == 0.5


def test_alias_absent_falls_back_to_the_committed_default(monkeypatch, tmp_path):
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("archive:\n  threshold: 0.6\n", encoding="utf-8")
    monkeypatch.delenv("TKB_ARCHIVE_THRESHOLD", raising=False)
    monkeypatch.delenv("ARCHIVE_THRESHOLD", raising=False)
    _patch_archive_env(monkeypatch, threshold=None)

    app = load_config(app_yaml)
    assert merge_archive_config(app).threshold == 0.6


def test_alias_live_deployment_env_still_controls_the_switch(monkeypatch, tmp_path):
    """线上 env 文件里的 ARCHIVE_ENABLED 不被派生规则动到。"""
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text("archive:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.delenv("TKB_ARCHIVE_ENABLED", raising=False)
    monkeypatch.setenv("ARCHIVE_ENABLED", "true")
    _patch_archive_env(monkeypatch, enabled=True)

    assert merge_archive_config(load_config(app_yaml)).enabled is True
