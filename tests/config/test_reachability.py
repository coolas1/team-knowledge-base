"""可达性扫描:每个叶子键都有可达的环境覆盖。

本文件守的是这次改动的动机本身——某个旋钮被评审、被写成"可配置",结果只能
靠改已提交的代码才改得动。扫描断言的是**可派生**,不是"有部署设过":每个
旋钮都有一个可达的覆盖名,无论有没有部署用它。
"""

from __future__ import annotations

import pytest

from config.overrides import derive_env_name, derived_names
from config.schema import load_config, runtime_config_path


def test_every_leaf_derives_a_reachable_override():
    for path, env_name in derived_names().items():
        # 失败信息指名出问题的路径,而不是只给一句断言失败。
        assert env_name == derive_env_name(path), (
            f"{path} 的覆盖名不是由路径派生的: {env_name}"
        )
        assert env_name.startswith("TKB_"), f"{path} 缺 TKB_ 前缀: {env_name}"
        assert len(env_name) > len("TKB_"), f"{path} 派生出空名: {env_name}"
        assert env_name == env_name.upper(), f"{path} 的名字不是大写: {env_name}"


def test_derived_names_are_unique_across_the_schema():
    names = derived_names()
    assert len(set(names.values())) == len(names)


@pytest.mark.parametrize(
    ("path", "env_name", "value", "default", "expected"),
    [
        ("engine.impl", "TKB_ENGINE_IMPL", "graphrag-alt", "graphrag",
         "graphrag-alt"),
        ("engine.ingest.chunk_concurrency",
         "TKB_ENGINE_INGEST_CHUNK_CONCURRENCY", "8", 4, 8),
        ("engine.ingest.llm_backoff_base_seconds",
         "TKB_ENGINE_INGEST_LLM_BACKOFF_BASE_SECONDS", "0.5", 2.0, 0.5),
        ("engine.memory.consolidation_batch_size",
         "TKB_ENGINE_MEMORY_CONSOLIDATION_BATCH_SIZE", "128", 64, 128),
        ("engine.memory.graph_worker",
         "TKB_ENGINE_MEMORY_GRAPH_WORKER", "false", True, False),
        ("plugin.impl", "TKB_PLUGIN_IMPL", "other", "tkb", "other"),
        ("archive.collision_policy",
         "TKB_ARCHIVE_COLLISION_POLICY", "block", "suffix", "block"),
    ],
)
def test_representative_paths_reach_the_environment(
    monkeypatch, tmp_path, path, env_name, value, default, expected
):
    """每个段至少一条路径:设了环境变量,有效配置就跟着变。"""
    missing = tmp_path / "app.yaml"  # 缺席 -> schema 默认值
    assert _leaf(load_config(missing), path) == default

    monkeypatch.setenv(env_name, value)

    resolved = _leaf(load_config(missing), path)
    assert resolved == expected
    assert type(resolved) is type(expected)


def test_previously_unreachable_key_is_settable_without_editing_the_file(
    monkeypatch, tmp_path
):
    """spec 场景:app.yaml 另有其值时,环境值对该部署生效,文件不变。"""
    text = "engine:\n  memory:\n    enabled: true\n    consolidation_batch_size: 32\n"
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(text, encoding="utf-8")

    assert load_config(app_yaml).engine.memory.consolidation_batch_size == 32

    monkeypatch.setenv("TKB_ENGINE_MEMORY_CONSOLIDATION_BATCH_SIZE", "64")
    assert load_config(app_yaml).engine.memory.consolidation_batch_size == 64
    assert app_yaml.read_text(encoding="utf-8") == text
    assert not runtime_config_path(app_yaml).exists()


def _leaf(cfg, path: str):
    node = cfg
    for part in path.split("."):
        node = getattr(node, part)
    return node
