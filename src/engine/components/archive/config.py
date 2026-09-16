"""归档有效配置：app.yaml 的 archive 段 + .env (ARCHIVE_*) 覆盖，env 优先。"""
from __future__ import annotations

from dataclasses import dataclass

from config.schema import AppConfig, ArchiveCfg
from config.settings import settings


@dataclass(frozen=True, slots=True)
class ArchiveRuntimeConfig:
    """归档流水线的有效配置（merge 后）。

    workspace 路径只来自 env（settings.archive.workspace_dir），
    行为旋钮来自 app.yaml，被 env 同名项覆盖。
    """

    enabled: bool
    workspace_dir: str
    threshold: float
    delta: float
    review_all: bool
    poll_seconds: float
    stability_checks: int
    max_attempts: int
    top_k: int
    collision_policy: str

    @property
    def inbox_dir(self) -> str:
        return f"{self.workspace_dir}/inbox"

    @property
    def archive_root(self) -> str:
        return f"{self.workspace_dir}/archive"

    def auto_threshold(self) -> float:
        """V2 自动执行下界；delta 仅保留用于旧配置兼容。"""
        return self.threshold

    def review_lower_bound(self) -> float:
        """V2 待确认范围下界固定为 0。"""
        return 0.0


def merge_archive_config(app: AppConfig) -> ArchiveRuntimeConfig:
    """app.yaml archive 段 + ARCHIVE_* env 覆盖 -> 有效配置。"""
    cfg: ArchiveCfg = app.archive
    env = settings.archive
    return ArchiveRuntimeConfig(
        enabled=env.enabled if env.enabled is not None else cfg.enabled,
        workspace_dir=env.workspace_dir,
        threshold=env.threshold if env.threshold is not None else cfg.threshold,
        delta=env.delta if env.delta is not None else cfg.delta,
        review_all=(
            env.review_all if env.review_all is not None else cfg.review_all
        ),
        poll_seconds=(
            env.poll_seconds if env.poll_seconds is not None else cfg.poll_seconds
        ),
        stability_checks=(
            env.stability_checks
            if env.stability_checks is not None
            else cfg.stability_checks
        ),
        max_attempts=(
            env.max_attempts
            if env.max_attempts is not None
            else cfg.max_attempts
        ),
        top_k=env.top_k if env.top_k is not None else cfg.top_k,
        collision_policy=(
            env.collision_policy
            if env.collision_policy is not None
            else cfg.collision_policy
        ),
    )
