"""Monotonic deadline primitives for bounded Hindsight operations."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class PhaseStatus(StrEnum):
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DeadlineBudget:
    """One total deadline shared by concurrent and sequential child phases."""

    total_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_at: float = 0.0

    def __post_init__(self) -> None:
        if self.total_seconds <= 0:
            raise ValueError("total_seconds must be positive")
        if self.started_at == 0.0:
            object.__setattr__(self, "started_at", self.clock())

    @property
    def deadline(self) -> float:
        return self.started_at + self.total_seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - self.clock())

    def phase_timeout(self, configured_seconds: float) -> float:
        if configured_seconds <= 0:
            raise ValueError("configured phase timeout must be positive")
        return min(configured_seconds, self.remaining())

    def elapsed_ms(self) -> float:
        return round((self.clock() - self.started_at) * 1000, 2)
