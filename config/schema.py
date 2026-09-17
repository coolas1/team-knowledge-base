"""App config: validates config/app.yaml and selects implementations.

有效配置由三层解析而成,逐键高者胜:

    config/app.yaml            已提交的默认值,运行中的应用绝不写它
            ↓ 被覆盖
    .env (环境变量)            每个部署自己的事实
            ↓ 被覆盖
    config/app.runtime.yaml    运行时改动(PUT /api/config)

层缺席即不参与贡献:没有运行时文件、也没设环境变量的部署,解析结果
恰好等于已提交的默认值。覆盖名由 config/overrides.py 从 schema 派生。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.engine.scope_policy import MemoryFeatures

from config.overrides import as_leaf_map, derived_names, env_overrides


class MemoryCfg(BaseModel):
    """Hindsight-as-capability flags on the single engine."""

    enabled: bool = False
    graph_worker: bool = True
    retain_max_concurrent: int = Field(default=1, ge=1)
    retain_chunk_concurrency: int = Field(default=4, ge=1, le=32)
    entity_resolution_max_concurrent: int = Field(default=8, ge=1, le=32)
    entity_resolution_timeout_seconds: float = Field(default=60, gt=0, le=600)
    consolidation_worker: bool = True
    consolidation_batch_size: int = Field(default=64, ge=1, le=1000)
    consolidation_observation_limit: int = Field(default=1000, ge=1)
    consolidation_max_iterations: int = Field(default=8, ge=1, le=100)
    consolidation_max_tokens: int = Field(default=32000, ge=1)
    consolidation_llm_timeout_seconds: float = Field(default=300, gt=0, le=600)
    consolidation_lease_seconds: int = Field(default=720, ge=60, le=3600)
    consolidation_max_output_tokens: int = Field(default=65536, ge=1)
    consolidation_max_cost_usd: float = Field(default=0, ge=0)
    consolidation_input_cost_usd_per_million: float = Field(default=0, ge=0)
    consolidation_output_cost_usd_per_million: float = Field(default=0, ge=0)
    consolidation_max_concurrent: int = Field(default=1, ge=1, le=32)
    consolidation_semantic_dedup: bool = True
    consolidation_semantic_threshold: float = Field(default=0.9, ge=0, le=1)
    fact_cache_capacity: int = Field(default=256, ge=0, le=10000)
    fact_cache_ttl_seconds: float = Field(default=1800, gt=0)
    fact_context_limit: int = Field(default=8, ge=1, le=100)
    fact_context_max_tokens: int = Field(default=1200, ge=1)
    recall_max_results: int = Field(default=100, ge=1, le=1000)
    recall_max_candidates: int = Field(default=300, ge=1, le=5000)
    recall_max_tokens: int = Field(default=4096, ge=1)
    mental_model_worker: bool = True
    mental_model_poll_seconds: float = Field(default=5, gt=0)
    mental_model_max_concurrent: int = Field(default=1, ge=1, le=32)
    mental_model_recall_results: int = Field(default=30, ge=1, le=1000)
    mental_model_max_evidence_tokens: int = Field(default=4096, ge=1)
    mental_model_max_output_tokens: int = Field(default=2048, ge=1)
    mental_model_lease_seconds: int = Field(default=300, ge=1)
    mental_model_max_attempts: int = Field(default=5, ge=1, le=100)
    mental_model_input_cost_usd_per_million: float = Field(default=0, ge=0)
    mental_model_output_cost_usd_per_million: float = Field(default=0, ge=0)
    mental_model_use_adaptive_reflect: bool = False
    reflect_max_iterations: int = Field(default=8, ge=1, le=100)
    reflect_max_tokens: int = Field(default=8192, ge=1)
    reflect_total_timeout_seconds: float = Field(default=60, gt=0, le=3600)
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

    vector_only: bool = True
    chunk_concurrency: int = Field(default=4, ge=1)
    doc_concurrency: int = Field(default=2, ge=1)
    # 模型端点瞬时失败（超时/连接错误/429/5xx）的有界重试预算。
    llm_retries: int = Field(default=3, ge=0)
    llm_backoff_base_seconds: float = Field(default=2.0, gt=0)


class EngineCfg(BaseModel):
    impl: str = "graphrag"
    config: str = "config/engine/graphrag"
    ingest: IngestCfg = Field(default_factory=IngestCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)


class PluginCfg(BaseModel):
    impl: str = "tkb"


class ArchiveCfg(BaseModel):
    """自动归档行为旋钮（app.yaml）。

    workspace 路径不在此处：它是部署路径，只来自 .env
    (ARCHIVE_WORKSPACE_DIR)，避免与 engine 的 UPLOAD_DIR 产生双源。
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    # V1 兼容字段；V2 二态分流不再使用 delta。
    delta: float = Field(default=0.10, ge=0.0, le=0.5)
    review_all: bool = False
    poll_seconds: float = Field(default=5.0, gt=0)
    stability_checks: int = Field(default=2, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    top_k: int = Field(default=5, ge=1)
    # 目标已有同名文件: suffix(确定性加后缀) | block(拒绝)
    collision_policy: str = Field(default="suffix", pattern="^(suffix|block)$")


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: EngineCfg = Field(default_factory=EngineCfg)
    plugin: PluginCfg = Field(default_factory=PluginCfg)
    archive: ArchiveCfg = Field(default_factory=ArchiveCfg)


#: 运行时层文件名;与被它覆盖的已提交文件同目录(见 runtime_config_path)。
RUNTIME_CONFIG_NAME = "app.runtime.yaml"

#: 环境层的文件层。`uv run` 的宿主运行与部署共用同一份 `.env`;真实环境变量
#: 优先级高于它,与 pydantic-settings 的 env > .env 一致。
DOTENV_PATH = Path(".env")

#: GET /api/config 为每个键报告的来源层。
SOURCE_DEFAULT = "default"  # 只有 schema 默认值,两个文件都没写
SOURCE_FILE = "app.yaml"  # 显式写在已提交的 config/app.yaml 里
SOURCE_ENV = "env"  # 派生环境名(TKB_*)在当前环境里
SOURCE_RUNTIME = "runtime"  # 写在 config/app.runtime.yaml 里


def runtime_config_path(app_path: Path) -> Path:
    """运行时层路径:与被它覆盖的已提交文件同目录。"""
    return app_path.with_name(RUNTIME_CONFIG_NAME)


def _read_yaml(path: Path) -> dict:
    """读 YAML 映射;文件缺席或不是映射时给出空层。"""
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _set_leaf(data: dict, path: str, value: object) -> None:
    """把点分路径的值写进嵌套字典,沿途补齐缺失的层。"""
    parts = path.split(".")
    cursor = data
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def _env_layer(dotenv_path: Path | None = None) -> dict[str, str]:
    """环境层里已表态的叶子:`.env` 的值被真实环境变量覆盖。

    `.env` 缺席时这一层就只是进程环境——与没有这一层之前完全一致。
    """
    merged: dict[str, str] = {}
    path = DOTENV_PATH if dotenv_path is None else Path(dotenv_path)
    if path.exists():
        merged.update(
            {
                name: value
                for name, value in dotenv_values(path).items()
                if value is not None
            }
        )
    merged.update(os.environ)
    return env_overrides(merged)


def load_config_with_sources(
    path: Path | str | None = None,
    dotenv_path: Path | str | None = None,
) -> tuple[AppConfig, dict[str, str]]:
    """解析三层,并给出每个叶子的来源层。

    环境值以原始字符串写入,类型转换交给 schema 的 pydantic 类型——转换失败
    时报错与从文件读入同一形状,并指出键名。
    """
    app_path = Path(path) if path is not None else Path("config/app.yaml")
    committed = _read_yaml(app_path)
    runtime = _read_yaml(runtime_config_path(app_path))
    from_env = _env_layer(Path(dotenv_path) if dotenv_path is not None else None)

    effective = copy.deepcopy(committed)
    for leaf_path, value in from_env.items():  # 环境层高于已提交默认值
        _set_leaf(effective, leaf_path, value)
    for leaf_path, value in as_leaf_map(runtime).items():  # 运行时层最高
        _set_leaf(effective, leaf_path, value)

    cfg = AppConfig.model_validate(effective)
    return cfg, _sources(committed, runtime, from_env)


def _sources(
    committed: dict, runtime: dict, from_env: dict[str, str]
) -> dict[str, str]:
    committed_leaves = as_leaf_map(committed)
    runtime_leaves = as_leaf_map(runtime)
    sources: dict[str, str] = {}
    for leaf_path in derived_names():
        if leaf_path in runtime_leaves:
            sources[leaf_path] = SOURCE_RUNTIME
        elif leaf_path in from_env:
            sources[leaf_path] = SOURCE_ENV
        elif leaf_path in committed_leaves:
            sources[leaf_path] = SOURCE_FILE
        else:
            sources[leaf_path] = SOURCE_DEFAULT
    return sources


def load_config(
    path: Path | str | None = None, dotenv_path: Path | str | None = None
) -> AppConfig:
    """有效配置:已提交默认值 + 环境覆盖 + 运行时覆盖。"""
    return load_config_with_sources(path, dotenv_path)[0]
