"""App config: validates config/app.yaml and selects implementations."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class EngineCfg(BaseModel):
    impl: str = "graphrag"
    config: str = "config/engine/graphrag"


class AgentCfg(BaseModel):
    harness: str = "codex"
    skills: list[str] = Field(
        default_factory=lambda: ["search_and_answer", "ingest_and_summarize"]
    )
    memory: dict = Field(default_factory=lambda: {"impl": None})


class FrontendCfg(BaseModel):
    impl: str = "webapp"


class WebappCfg(BaseModel):
    engine_access: Literal["inprocess", "mcp"] = "inprocess"


class AppConfig(BaseModel):
    engine: EngineCfg = Field(default_factory=EngineCfg)
    agent: AgentCfg = Field(default_factory=AgentCfg)
    frontend: FrontendCfg = Field(default_factory=FrontendCfg)
    webapp: WebappCfg = Field(default_factory=WebappCfg)


def load_config(path: Path | str | None = None) -> AppConfig:
    """Load AppConfig from a YAML file; missing file yields defaults."""
    p = Path(path) if path is not None else Path("config/app.yaml")
    data: dict = {}
    if p.exists():
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
        if loaded:
            data = loaded
    return AppConfig.model_validate(data)
