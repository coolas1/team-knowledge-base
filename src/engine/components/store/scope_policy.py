"""Atomic policy publication; callers supply a previously authorized bank."""

from dataclasses import dataclass

from sqlalchemy import select, update, func

from src.engine.components.store.models import MemoryBank
from src.engine.scope import MemoryScope
from src.engine.scope_policy import ScopePolicy


class PolicyVersionConflict(ValueError):
    pass


@dataclass(frozen=True)
class PolicySnapshot:
    bank_id: str
    version: int
    policy: ScopePolicy


class ScopePolicyStore:
    def __init__(self, session_factory, *, scope: MemoryScope | None = None):
        self._sessions = session_factory
        self.scope = scope or MemoryScope()

    def with_scope(self, scope: MemoryScope):
        return ScopePolicyStore(self._sessions, scope=scope)

    async def read(self) -> PolicySnapshot:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(MemoryBank).where(MemoryBank.id == self.scope.bank_id)
                )
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("bank does not exist")
            return PolicySnapshot(
                row.id, row.policy_version, ScopePolicy.model_validate(row.config)
            )

    async def publish(
        self, policy: ScopePolicy, *, expected_version: int
    ) -> PolicySnapshot:
        # Validate again at the persistence boundary even for model_construct callers.
        policy = ScopePolicy.model_validate(policy.model_dump())
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        async with self._sessions() as session:
            version = (
                await session.execute(
                    update(MemoryBank)
                    .where(
                        MemoryBank.id == self.scope.bank_id,
                        MemoryBank.policy_version == expected_version,
                    )
                    .values(
                        config=policy.model_dump(mode="json"),
                        policy_version=MemoryBank.policy_version + 1,
                        updated_at=func.now(),
                    )
                    .returning(MemoryBank.policy_version)
                )
            ).scalar_one_or_none()
            if version is None:
                raise PolicyVersionConflict("bank missing or policy version changed")
            await session.commit()
        return PolicySnapshot(self.scope.bank_id, version, policy)
