"""ActionPlan 构造与执行前安全校验（design.md D5：确定性 Validator）。

校验规则（任一硬性失败即阻止执行）：
- 工作区边界：解析后的目标必须在 archive 根之内
- 路径穿越：new_subdirectory 不含 .. / 绝对路径 / 盘符
- 目标冲突：policy=suffix 时确定性加后缀，policy=block 时拒绝
- 源指纹：源文件存在且 sha256 等于分类时的 hash
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .classifier import ArchiveDecision
from .hashing import file_sha256, file_sha256_async
from .jobs import ClaimedJob


class PlanError(Exception):
    """构造或校验失败 —— plan 被阻止执行。"""


def safe_component(name: str) -> str:
    """把调用方/LLM 提供的名字坍缩为单个路径组件（与 backend._safe_filename 同语义）。"""
    base = Path(name).name
    if base in ("", ".", ".."):
        raise PlanError(f"非法文件名: {name!r}")
    return base


@dataclass(slots=True)
class ActionPlan:
    source_path: Path
    content_hash: str
    destination_dir: Path  # 绝对路径（archive 根之内）
    destination_path: Path  # dir / new_name（冲突解析后的最终路径）
    new_name: str
    creates_directory: bool  # 是否需要新建目录（new_subdirectory）
    decision: ArchiveDecision
    validation_notes: list[str] = field(default_factory=list)


def _resolve_new_subdirectory(raw: str, *, max_depth: int = 2) -> PurePosixPath:
    """校验 LLM 提议的新目录：相对、无穿越、层级受 policy 限制。"""
    if raw.startswith(("/", "\\")):
        raise PlanError(f"new_subdirectory 不能是绝对路径: {raw!r}")
    cleaned = raw.strip().strip("/")
    if not cleaned:
        raise PlanError("new_subdirectory 为空")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p in ("..",) or "\\" in p or len(p) > 64 for p in parts):
        raise PlanError(f"new_subdirectory 非法: {raw!r}")
    if len(parts) > max_depth:
        raise PlanError(f"new_subdirectory 最多 {max_depth} 级: {raw!r}")
    return PurePosixPath(*parts)


def build_plan(
    job: ClaimedJob,
    decision: ArchiveDecision,
    archive_root: Path,
    *,
    candidate_dirs: dict[str, Path] | None = None,
    max_directory_depth: int = 2,
) -> ActionPlan:
    """由决策构造行动计划：candidate_id 引用或允许根下的新目录。

    candidate_dirs: candidate_id -> 已存在的目录绝对路径（来自画像存储）。
    """
    root = archive_root.resolve()

    if decision.candidate_id is not None:
        candidates = candidate_dirs or {}
        target = candidates.get(decision.candidate_id)
        if target is None:
            raise PlanError(f"候选目录不存在: {decision.candidate_id}")
        destination_dir = target.resolve()
        creates_directory = False
    elif decision.new_subdirectory is not None:
        rel = _resolve_new_subdirectory(
            decision.new_subdirectory, max_depth=max_directory_depth
        )
        destination_dir = (root / Path(*rel.parts)).resolve()
        creates_directory = True
        if root not in destination_dir.parents:
            raise PlanError(f"新目录越界: {decision.new_subdirectory!r}")
    else:
        raise PlanError("决策既无 candidate_id 也无 new_subdirectory")

    if destination_dir == root or root not in destination_dir.parents:
        raise PlanError(f"目标目录越界: {destination_dir}")

    new_name = safe_component(
        decision.new_name if decision.new_name else Path(job.file_path).name
    )
    return ActionPlan(
        source_path=Path(job.file_path),
        content_hash=job.content_hash,
        destination_dir=destination_dir,
        destination_path=destination_dir / new_name,
        new_name=new_name,
        creates_directory=creates_directory,
        decision=decision,
    )


def validate_plan(
    plan: ActionPlan,
    archive_root: Path,
    *,
    collision_policy: str = "suffix",
) -> ActionPlan:
    """就地校验并解析冲突；硬性失败抛 PlanError。

    返回同一 plan（destination_path 可能因 suffix 策略而调整）。
    """
    if not plan.source_path.is_file():
        raise PlanError(f"源文件不存在: {plan.source_path}")
    return _validate_plan_with_digest(
        plan,
        archive_root,
        digest=file_sha256(plan.source_path),
        collision_policy=collision_policy,
    )


def _validate_plan_with_digest(
    plan: ActionPlan,
    archive_root: Path,
    *,
    digest: str,
    collision_policy: str,
) -> ActionPlan:
    """Finish deterministic validation with a precomputed source digest."""
    root = archive_root.resolve()
    resolved_dir = plan.destination_dir.resolve()
    if resolved_dir == root or root not in resolved_dir.parents:
        raise PlanError(f"目标越界: {resolved_dir}")
    if digest != plan.content_hash:
        raise PlanError("源文件在分类后发生变化，中止执行")

    # 3. 冲突：suffix 确定性加后缀，block 拒绝；绝不覆盖。
    if plan.destination_path.exists():
        if collision_policy == "block":
            raise PlanError(f"目标已存在同名文件: {plan.destination_path}")
        stem = plan.destination_path.stem
        suffix = plan.destination_path.suffix
        for i in range(1, 1000):
            candidate = plan.destination_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                plan.validation_notes.append(
                    f"目标冲突，重命名为 {candidate.name}"
                )
                plan.destination_path = candidate
                plan.new_name = candidate.name
                break
        else:
            raise PlanError(f"冲突解析失败（过多同名文件）: {plan.destination_dir}")

    # 4. 新建目录时父链必须仍在 archive 根内（防 TOCTOU 穿越二次确认）
    if plan.creates_directory and root not in resolved_dir.parents:
        raise PlanError("新建目录越界")
    return plan


async def validate_plan_async(
    plan: ActionPlan,
    archive_root: Path,
    *,
    collision_policy: str = "suffix",
) -> ActionPlan:
    """Async validator used by runtime paths so hashing never blocks the loop."""
    if not plan.source_path.is_file():
        raise PlanError(f"源文件不存在: {plan.source_path}")
    return _validate_plan_with_digest(
        plan,
        archive_root,
        digest=await file_sha256_async(plan.source_path),
        collision_policy=collision_policy,
    )
