"""Typed, sanitized failures returned by bounded deep recall."""

from __future__ import annotations

from typing import Any


class DeepSearchError(RuntimeError):
    code = "deep_search_error"

    def __init__(self, search_id: str, trace: dict[str, Any]) -> None:
        self.search_id = search_id
        self.trace = trace
        super().__init__(f"{self.code} search_id={search_id}")

    def as_payload(self) -> dict[str, Any]:
        return {
            "strategy_used": "recall",
            "sources": [],
            "related_entities": [],
            "based_on": {},
            "trace": self.trace,
            "error": {"code": self.code, "search_id": self.search_id},
        }


class DeepSearchTimeoutError(DeepSearchError):
    code = "deep_search_timeout"


class DeepSearchUnavailableError(DeepSearchError):
    code = "deep_search_unavailable"
