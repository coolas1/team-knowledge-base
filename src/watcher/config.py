"""目录监控配置加载。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path("config/watch_config.yaml")


@dataclass
class DirectoryConfig:
    """单个监控目录配置。"""
    path: str
    recursive: bool = True


@dataclass
class PipelineScheduleConfig:
    """Pipeline 调度配置。"""
    schedule_hours: int = 12  # 自动同步间隔（小时），0 表示禁用
    enabled: bool = True


@dataclass
class WatchConfig:
    """目录监控总配置。"""
    enabled: bool = False
    directories: list[DirectoryConfig] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    pipeline: PipelineScheduleConfig = field(default_factory=PipelineScheduleConfig)


def load_watch_config() -> WatchConfig:
    """从 config/watch_config.yaml 加载配置。

    文件不存在或 enabled=false 时返回默认配置（不启用监控）。
    """
    if not CONFIG_PATH.exists():
        return WatchConfig()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    watch = data.get("watch", {})
    if not watch:
        return WatchConfig()

    directories = [
        DirectoryConfig(path=d["path"], recursive=d.get("recursive", True))
        for d in watch.get("directories", [])
        if d.get("path")
    ]

    pipeline_data = watch.get("pipeline", {})
    pipeline = PipelineScheduleConfig(
        schedule_hours=pipeline_data.get("schedule_hours", 12),
        enabled=pipeline_data.get("enabled", True),
    )

    return WatchConfig(
        enabled=watch.get("enabled", False),
        directories=directories,
        exclude_patterns=watch.get("exclude_patterns", []),
        pipeline=pipeline,
    )
