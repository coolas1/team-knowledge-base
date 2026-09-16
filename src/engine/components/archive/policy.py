"""可编辑、版本化的归档策略。"""
from __future__ import annotations

from sqlalchemy import func, select, update

from src.engine.components.store.models import ArchivePolicy
from src.engine.components.store.postgres import async_session_factory


DEFAULT_RULES = {
    "strategy": "project_first",
    "instructions": (
        "优先识别文件所属项目并按项目归档；无法识别项目时再按文档主题归档；"
        "目录最多两级，名称应稳定、简短并可供团队复用。"
    ),
    "allow_new_directories": True,
    "max_directory_depth": 2,
}


class ArchivePolicyStore:
    async def current(self) -> ArchivePolicy:
        async with async_session_factory() as session:
            row = await session.scalar(
                select(ArchivePolicy)
                .where(ArchivePolicy.active.is_(True))
                .order_by(ArchivePolicy.version.desc())
                .limit(1)
            )
            if row is not None:
                return row
            row = ArchivePolicy(version=1, enabled=True, active=True, rules=DEFAULT_RULES)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def replace(self, *, enabled: bool, rules: dict) -> ArchivePolicy:
        if not isinstance(rules, dict):
            raise ValueError("rules 必须是对象")
        instructions = rules.get("instructions")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("rules.instructions 不能为空")
        max_depth = rules.get("max_directory_depth", DEFAULT_RULES["max_directory_depth"])
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 1 <= max_depth <= 10:
            raise ValueError("rules.max_directory_depth 必须是 1 到 10 的整数")
        clean_rules = {**DEFAULT_RULES, **rules, "instructions": instructions.strip()}
        async with async_session_factory() as session:
            next_version = int(
                await session.scalar(select(func.max(ArchivePolicy.version))) or 0
            ) + 1
            await session.execute(
                update(ArchivePolicy)
                .where(ArchivePolicy.active.is_(True))
                .values(active=False)
            )
            row = ArchivePolicy(
                version=next_version,
                enabled=enabled,
                active=True,
                rules=clean_rules,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row


def policy_to_dict(row: ArchivePolicy) -> dict:
    return {
        "id": str(row.id),
        "version": row.version,
        "enabled": row.enabled,
        "rules": row.rules,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
