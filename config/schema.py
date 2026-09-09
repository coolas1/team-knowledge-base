"""App config: validates config/app.yaml and selects implementations."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.engine.scope_policy import MemoryFeatures


class MemoryCfg(BaseModel):
    """Hindsight-as-capability flags on the single engine."""

    enabled: bool = False
    graph_worker: bool = True
    retain_max_concurrent: int = Field(default=1, ge=1)
    consolidation_worker: bool = True
    consolidation_batch_size: int = Field(default=64, ge=1, le=1000)
    consolidation_observation_limit: int = Field(default=1000, ge=1)
    consolidation_max_iterations: int = Field(default=8, ge=1, le=100)
    consolidation_max_tokens: int = Field(default=32000, ge=1)
    consolidation_max_cost_usd: float = Field(default=0, ge=0)
    consolidation_input_cost_usd_per_million: float = Field(default=0, ge=0)
    consolidation_output_cost_usd_per_million: float = Field(default=0, ge=0)
    consolidation_max_concurrent: int = Field(default=1, ge=1, le=32)
    consolidation_semantic_dedup: bool = True
    consolidation_semantic_threshold: float = Field(default=0.9, ge=0, le=1)
    recall_max_results: int = Field(default=100, ge=1, le=1000)
    recall_max_candidates: int = Field(default=300, ge=1, le=5000)
    recall_max_tokens: int = Field(default=4096, ge=1)
    features: MemoryFeatures = Field(default_factory=MemoryFeatures)

    @model_validator(mode="after")
    def validate_features(self):
        if self.features.scope and not self.enabled:
            raise ValueError("memory features require memory.enabled")
        if self.consolidation_max_cost_usd and not (
            self.consolidation_input_cost_usd_per_million
            or self.consolidation_output_cost_usd_per_million
        ):
            raise ValueError("consolidation cost limit requires token prices")
        return self


class IngestCfg(BaseModel):
    """Ingest parallelism knobs (1 restores fully serial behavior)."""

    chunk_concurrency: int = Field(default=4, ge=1)
    doc_concurrency: int = Field(default=2, ge=1)


class EngineCfg(BaseModel):
    impl: str = "graphrag"
    config: str = "config/engine/graphrag"
    ingest: IngestCfg = Field(default_factory=IngestCfg)
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
