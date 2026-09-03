"""App config: validates config/app.yaml and selects implementations."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class MemoryCfg(BaseModel):
    """Hindsight-as-capability flags on the single engine."""

    enabled: bool = False
    graph_worker: bool = True
    retain_max_concurrent: int = Field(default=1, ge=1)


class EngineCfg(BaseModel):
    impl: str = "graphrag"
    config: str = "config/engine/graphrag"
    memory: MemoryCfg = Field(default_factory=MemoryCfg)


class PluginCfg(BaseModel):
    impl: str = "tkb"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: EngineCfg = Field(default_factory=EngineCfg)
    plugin: PluginCfg = Field(default_factory=PluginCfg)


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load AppConfig from a YAML file; missing file yields defaults."""
    p = Path(path) if path is not None else Path("config/app.yaml")
    data: dict = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
        if loaded:
            data = loaded
    return AppConfig.model_validate(data)
