"""派生环境覆盖名：schema 叶子路径 -> 环境变量名。

一条规则,从 schema 生成,永不反解析回路径:

    TKB_ + "_".join(path_parts).upper()

即 `engine.ingest.chunk_concurrency` 由 `TKB_ENGINE_INGEST_CHUNK_CONCURRENCY`
覆盖。名字只由**已存在于 schema 的路径**生成,再去环境里查存在与否;任何环境
名都不会被反解析成路径。因此键名里的下划线(`chunk_concurrency`)与路径分隔符
不可能混淆——解析式方案才有的歧义在这里根本不会出现。`find_collisions` 是这条
性质随 schema 增长仍然成立的保证:两条路径共用一个名字时会测试失败,而不是
静默地一者取到另一者的值。

`ARCHIVE_*` 是唯一早于本规则的前缀族,见 ARCHIVE_ENV_ALIASES。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from typing import Any

from pydantic import BaseModel

ENV_PREFIX = "TKB_"

# 早于派生规则的前缀族:九个 ARCHIVE_* 名对应 app.yaml 的 archive.* 键。
# 两个在线部署(production :8000、staging :8001)的 env 文件都在用它们,
# 所以这里保留为有文档的别名而不是改名——改名会在 env 文件未同步时静默丢掉
# 线上设置。二者同时设置时显式别名胜出(由 merge_archive_config 保证),
# 顺序为 ARCHIVE_* -> TKB_ARCHIVE_* -> app.yaml。
#
# archive.workspace_dir 不在此表内:它是部署路径,app.yaml 里没有对应键,
# 只来自环境(ARCHIVE_WORKSPACE_DIR)。
ARCHIVE_ENV_ALIASES: dict[str, str] = {
    "archive.enabled": "ARCHIVE_ENABLED",
    "archive.threshold": "ARCHIVE_THRESHOLD",
    "archive.delta": "ARCHIVE_DELTA",
    "archive.review_all": "ARCHIVE_REVIEW_ALL",
    "archive.poll_seconds": "ARCHIVE_POLL_SECONDS",
    "archive.stability_checks": "ARCHIVE_STABILITY_CHECKS",
    "archive.max_attempts": "ARCHIVE_MAX_ATTEMPTS",
    "archive.top_k": "ARCHIVE_TOP_K",
    "archive.collision_policy": "ARCHIVE_COLLISION_POLICY",
}


def derive_env_name(path: str) -> str:
    """叶子路径 -> 覆盖名:`engine.ingest.chunk_concurrency` -> `TKB_...`。"""
    parts = [part for part in path.split(".") if part]
    return ENV_PREFIX + "_".join(parts).upper()


def iter_leaf_paths(
    model: type[BaseModel], prefix: str = ""
) -> Iterator[str]:
    """深度优先产出模型下每个叶子字段的点分路径。"""
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        nested = field.annotation
        if isinstance(nested, type) and issubclass(nested, BaseModel):
            yield from iter_leaf_paths(nested, f"{path}.")
        else:
            yield path


def derived_names(model: type[BaseModel] | None = None) -> dict[str, str]:
    """`{叶子路径: 派生环境名}`,覆盖 AppConfig 的每个叶子。"""
    if model is None:
        # 局部导入避免 config.schema <-> config.overrides 的模块级循环。
        from config.schema import AppConfig

        model = AppConfig
    return {path: derive_env_name(path) for path in iter_leaf_paths(model)}


def find_collisions(names: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    """共用一个环境名的路径集合(正常情况为空)。"""
    mapping = derived_names() if names is None else names
    by_name: dict[str, list[str]] = {}
    for path, env_name in mapping.items():
        by_name.setdefault(env_name, []).append(path)
    return {
        env_name: sorted(paths)
        for env_name, paths in by_name.items()
        if len(paths) > 1
    }


def assert_no_collisions(names: Mapping[str, str] | None = None) -> None:
    """两条路径共用一个覆盖名时抛错,并把它们都报出来。"""
    collisions = find_collisions(names)
    if collisions:
        detail = "; ".join(
            f"{env_name} <- {', '.join(paths)}"
            for env_name, paths in sorted(collisions.items())
        )
        raise ValueError(f"派生环境名冲突: {detail}")


def env_overrides(
    environ: Mapping[str, str] | None = None,
    model: type[BaseModel] | None = None,
) -> dict[str, str]:
    """环境里已表态的叶子:`{叶子路径: 原始字符串值}`。

    只查名字的存在性,不做类型转换——转换交给 schema 的 pydantic 类型,
    失败时报错与从文件读入时一致,并指出键名。
    """
    env = os.environ if environ is None else environ
    return {
        path: env[env_name]
        for path, env_name in derived_names(model).items()
        if env_name in env
    }


def unused_archive_aliases(
    aliases: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """别名表中不指向真实 archive 叶子的条目(正常情况为空)。"""
    table = ARCHIVE_ENV_ALIASES if aliases is None else aliases
    known = derived_names()
    return {
        path: env_name
        for path, env_name in table.items()
        if path not in known or not path.startswith("archive.")
    }


def as_leaf_map(data: Any, prefix: str = "") -> dict[str, Any]:
    """取 YAML 数据里显式出现的叶子:`{点分路径: 原始值}`。"""
    if not isinstance(data, dict):
        return {prefix.rstrip("."): data}
    leaves: dict[str, Any] = {}
    for key, value in data.items():
        leaves.update(as_leaf_map(value, f"{prefix}{key}."))
    return leaves
