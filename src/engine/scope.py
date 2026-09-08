"""Runtime-neutral memory ownership and tag filter contracts.

Filters select evidence; they do not grant access. A host must resolve the
trusted scope separately and intersect it with any caller-supplied filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DEFAULT_BANK_ID = "default-team"
TagsMatch = Literal["any", "all", "any_strict", "all_strict", "exact"]
MATCH_MODES = frozenset({"any", "all", "any_strict", "all_strict", "exact"})


def _tags(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise ValueError("tags must be a list or tuple of nonempty strings")
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("tags must contain nonempty strings")
    return tuple(sorted(set(values)))


@dataclass(frozen=True, slots=True)
class TagFilter:
    """One tag-group leaf. Empty groups retain set-operator semantics.

    Top-level API absence is represented by None, not by an empty leaf.
    This matters for strict empty leaves and exact empty scope selection.
    """

    tags: tuple[str, ...] = ()
    match: TagsMatch = "any"

    def __post_init__(self) -> None:
        if self.match not in MATCH_MODES:
            raise ValueError(f"unsupported tags_match: {self.match}")
        object.__setattr__(self, "tags", _tags(self.tags))

    def matches(self, tags: tuple[str, ...] | list[str] | None) -> bool:
        actual = set(tags or ())
        wanted = set(self.tags)
        if self.match == "exact":
            return actual == wanted
        if not actual:
            return self.match in {"any", "all"}
        if self.match in {"any", "any_strict"}:
            return bool(actual & wanted)
        return wanted <= actual


@dataclass(frozen=True, slots=True)
class TagGroup:
    operator: Literal["and", "or", "not"]
    filters: tuple[TagExpression, ...]

    def __post_init__(self) -> None:
        if self.operator not in {"and", "or", "not"}:
            raise ValueError("unsupported tag group operator")
        if not isinstance(self.filters, (list, tuple)) or not self.filters:
            raise ValueError("tag groups require children")
        if self.operator == "not" and len(self.filters) != 1:
            raise ValueError("not requires exactly one child")
        if any(not isinstance(f, (TagFilter, TagGroup)) for f in self.filters):
            raise ValueError("invalid tag group child")
        object.__setattr__(self, "filters", tuple(self.filters))

    def matches(self, tags: tuple[str, ...] | list[str] | None) -> bool:
        if self.operator == "not":
            return not self.filters[0].matches(tags)
        results = (child.matches(tags) for child in self.filters)
        return all(results) if self.operator == "and" else any(results)


type TagExpression = TagFilter | TagGroup


def parse_tag_expression(value: dict, *, _depth: int = 0) -> TagExpression:
    """Parse upstream-shaped groups, rejecting ambiguous or excessive input."""
    if _depth > 12 or not isinstance(value, dict):
        raise ValueError("invalid or excessively nested tag expression")
    if "tags" in value:
        if set(value) - {"tags", "match"}:
            raise ValueError("unknown tag filter fields")
        return TagFilter(tags=value["tags"], match=value.get("match", "any"))
    if len(value) != 1:
        raise ValueError("tag group must have exactly one operator")
    operator, children = next(iter(value.items()))
    if operator not in {"and", "or", "not"}:
        raise ValueError("unsupported tag group operator")
    if operator == "not":
        children = [children]
    if not isinstance(children, list) or not 1 <= len(children) <= 64:
        raise ValueError("tag group requires 1..64 children")
    return TagGroup(
        operator=operator,
        filters=tuple(parse_tag_expression(c, _depth=_depth + 1) for c in children),
    )


def request_tag_filter(
    tags: tuple[str, ...] = (), match: TagsMatch = "any"
) -> TagExpression | None:
    """Top-level empty tags mean no filter except for exact, as upstream."""
    leaf = TagFilter(tags, match)
    return leaf if leaf.tags or match == "exact" else None


@dataclass(frozen=True, slots=True)
class MemoryScope:
    bank_id: str = DEFAULT_BANK_ID
    visibility: TagExpression | None = None
    subject_id: str | None = None
    agent_name: str | None = None
    policy_version: int = 1
    observation_scopes: tuple[tuple[str, ...], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.bank_id, str) or not self.bank_id.strip():
            raise ValueError("bank_id must be nonempty")
        if self.bank_id != self.bank_id.strip():
            raise ValueError("bank_id must not have surrounding whitespace")
        if not isinstance(self.policy_version, int) or self.policy_version < 1:
            raise ValueError("policy_version must be positive")
        if self.visibility is not None and not isinstance(
            self.visibility, (TagFilter, TagGroup)
        ):
            raise ValueError("visibility must be a tag expression")
        object.__setattr__(
            self,
            "observation_scopes",
            tuple(dict.fromkeys(_tags(tags) for tags in self.observation_scopes)),
        )

    def permits(self, bank_id: str, tags: list[str] | tuple[str, ...] = ()) -> bool:
        return bank_id == self.bank_id and (
            self.visibility is None or self.visibility.matches(tags)
        )

    def narrowed(self, requested: TagExpression | None) -> MemoryScope:
        """Intersect a query filter with trusted visibility; never replace it."""
        from dataclasses import replace

        visibility = self.visibility
        if requested is not None:
            visibility = (
                requested
                if visibility is None
                else TagGroup("and", (visibility, requested))
            )
        return replace(self, visibility=visibility)
