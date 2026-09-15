"""Deterministic publication policy for authoritative conversation memory."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Mapping


_ELIGIBLE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "preference": (
        re.compile(r"(?:我|我们)(?:更?喜欢|偏好|不喜欢|希望|习惯|倾向)"),
        re.compile(r"\b(?:i|we)\s+(?:prefer|like|dislike|want|usually)\b", re.I),
        re.compile(r"(?:私|私たち)(?:は|が).*(?:好み|好き|嫌い|希望)"),
    ),
    "decision": (
        re.compile(r"(?:我|我们)(?:决定|确定|选定|同意采用)"),
        re.compile(r"\b(?:i|we)\s+(?:decided|chose|agreed)\b", re.I),
        re.compile(r"(?:決めた|決定した|選んだ)"),
    ),
    "commitment": (
        re.compile(r"(?:我|我们)(?:会|将|承诺|负责|计划)"),
        re.compile(r"\b(?:i|we)\s+(?:will|commit|promise|plan)\b", re.I),
        re.compile(r"(?:約束する|予定です|担当する)"),
    ),
    "state": (
        re.compile(r"(?:我|我们)(?:正在|已经|目前|现在|完成了|使用的是)"),
        re.compile(r"\b(?:i am|we are|i have|we have|currently|my .* is)\b", re.I),
        re.compile(r"(?:現在|完了した|使っています)"),
    ),
}

_DISALLOWED_PATTERNS = (
    re.compile(r"^(?:谢谢|好的|收到|明白|ok|thanks?|thank you)[！!。.\s]*$", re.I),
    re.compile(r"(?:忽略.*指令|系统提示词|tool[_ -]?result|<untrusted_)", re.I),
)


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    version: int = 1
    retain_user_types: tuple[str, ...] = (
        "preference",
        "decision",
        "commitment",
        "state",
    )
    retain_assistant_text: bool = False
    require_explicit_signal: bool = True

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("retention policy version must be positive")
        unknown = set(self.retain_user_types) - set(_ELIGIBLE_PATTERNS)
        if unknown:
            raise ValueError(f"unsupported retention types: {sorted(unknown)}")
        if self.retain_assistant_text:
            raise ValueError("authoritative assistant-text publication is not supported")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RetentionPolicy":
        allowed = {
            "version",
            "retain_user_types",
            "retain_assistant_text",
            "require_explicit_signal",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown retention policy fields: {sorted(unknown)}")
        types = value.get("retain_user_types", cls().retain_user_types)
        if not isinstance(types, (list, tuple)) or not all(
            isinstance(item, str) for item in types
        ):
            raise ValueError("retain_user_types must be a string list")
        return cls(
            version=int(value.get("version", 1)),
            retain_user_types=tuple(types),
            retain_assistant_text=bool(value.get("retain_assistant_text", False)),
            require_explicit_signal=bool(value.get("require_explicit_signal", True)),
        )

    def classify_user_text(self, text: str) -> tuple[str, ...]:
        normalized = text.strip()
        if not normalized or any(pattern.search(normalized) for pattern in _DISALLOWED_PATTERNS):
            return ()
        matched = tuple(
            memory_type
            for memory_type in self.retain_user_types
            if any(pattern.search(normalized) for pattern in _ELIGIBLE_PATTERNS[memory_type])
        )
        if self.require_explicit_signal:
            return matched
        return matched or ("state",)
