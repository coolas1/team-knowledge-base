"""Agent memory abstraction - interface ONLY. No implementation (spec §1, §2).

The agent decides where its memory lives at runtime; no concrete store is
built in this effort. No coupling to the engine.
"""
from __future__ import annotations

from typing import Protocol


class MemoryStore(Protocol):
    async def remember(self, key: str, value: str) -> None: ...
    async def recall(self, key: str) -> str | None: ...
