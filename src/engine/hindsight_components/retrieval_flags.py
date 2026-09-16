"""Resolve retrieval rollout switches for the current memory bank."""

from src.engine.components.store.models import MemoryBank


async def bank_read_enabled(
    sessions,
    scope,
    flag: str,
    *,
    process_enabled: bool,
) -> bool:
    """Require both the process switch and the bank migration switch.

    A missing bank or flag is deliberately fail-closed so a partially migrated
    bank keeps using the legacy read path.
    """
    if not process_enabled:
        return False
    async with sessions() as session:
        bank = await session.get(MemoryBank, scope.bank_id)
    return bool(bank and (bank.config or {}).get(flag) is True)
