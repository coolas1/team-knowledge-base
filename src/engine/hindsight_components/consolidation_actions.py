"""Validate untrusted consolidation plans against a versioned evidence read set.

Database publication must recheck these versions under its scope lock. Validation
here is deliberately independent of the model provider and never grants scope.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.engine.scope import MemoryScope


class ConsolidationAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["create", "update", "delete"]
    observation_id: str | None = None
    text: str = Field(default="", max_length=16000)
    source_fact_ids: list[str] = Field(default_factory=list, max_length=256)
    change: Literal["synthesis", "change", "conflict"] = "synthesis"
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.action == "create":
            if self.observation_id is not None:
                raise ValueError("create cannot select an observation ID")
        elif not self.observation_id:
            raise ValueError("update/delete require an observation ID")
        if self.action != "delete" and (
            not self.text.strip() or not self.source_fact_ids
        ):
            raise ValueError("create/update require text and source facts")
        if self.action == "delete" and (self.text or not self.reason.strip()):
            raise ValueError("delete requires a reason and no replacement text")
        if any(not value.strip() for value in self.source_fact_ids):
            raise ValueError("empty source fact ID")
        return self


class ConsolidationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actions: list[ConsolidationAction] = Field(max_length=128)


@dataclass(frozen=True, slots=True)
class EvidenceVersion:
    id: str
    version: int
    bank_id: str
    scope_tags: tuple[str, ...]
    state: str = "active"


@dataclass(frozen=True, slots=True)
class ObservationVersion(EvidenceVersion):
    source_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    action: ConsolidationAction
    expected_version: int | None
    source_versions: tuple[tuple[str, int], ...]


def exact_key(text: str) -> str:
    """Normalize presentation only; preserve punctuation, case and negation."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def validate_actions(
    payload: dict,
    *,
    scope: MemoryScope,
    write_scope: tuple[str, ...],
    facts: dict[str, EvidenceVersion],
    observations: dict[str, ObservationVersion],
) -> tuple[ValidatedAction, ...]:
    """Reject a whole invalid plan, then coalesce repeated actions deterministically.

    Updates preserve the target's still-valid evidence. Conflicting update/delete
    commands are rejected rather than silently choosing destructive behavior.
    """
    canonical_scope = tuple(sorted(set(write_scope)))
    if canonical_scope not in (scope.observation_scopes or ((),)):
        raise ValueError("unauthorized consolidation write scope")
    plan = ConsolidationPlan.model_validate(payload)
    merged: dict[tuple[str, str], ConsolidationAction] = {}
    for action in plan.actions:
        target = None
        if action.observation_id is not None:
            target = observations.get(action.observation_id)
            if (
                target is None
                or target.id != action.observation_id
                or target.version < 1
                or target.state not in {"active", "stale"}
                or target.bank_id != scope.bank_id
                or tuple(sorted(set(target.scope_tags))) != canonical_scope
                or not scope.permits(target.bank_id, target.scope_tags)
            ):
                raise ValueError("observation is outside the writable read set")
        source_ids = list(action.source_fact_ids)
        if target is not None and action.action == "update":
            source_ids.extend(target.source_fact_ids)
        source_ids = sorted(set(source_ids))
        for source_id in source_ids:
            fact = facts.get(source_id)
            if (
                fact is None
                or fact.id != source_id
                or fact.version < 1
                or fact.state != "active"
                or not scope.permits(fact.bank_id, fact.scope_tags)
                or not set(canonical_scope).issubset(fact.scope_tags)
            ):
                raise ValueError("source fact is outside the valid read set")
        key = (
            ("target", action.observation_id)
            if action.observation_id is not None
            else ("create", exact_key(action.text))
        )
        previous = merged.get(key)
        if previous is not None:
            if previous.action != action.action:
                raise ValueError("conflicting actions for one observation")
            source_ids = sorted(set(source_ids) | set(previous.source_fact_ids))
            # Never silently lose a conflict/change marker while coalescing.
            priority = {"synthesis": 0, "change": 1, "conflict": 2}
            change = max((previous.change, action.change), key=priority.__getitem__)
        else:
            change = action.change
        merged[key] = action.model_copy(
            update={"source_fact_ids": source_ids, "change": change}
        )
    return tuple(
        ValidatedAction(
            action=action,
            expected_version=(
                observations[action.observation_id].version
                if action.observation_id is not None
                else None
            ),
            source_versions=tuple(
                (source_id, facts[source_id].version)
                for source_id in action.source_fact_ids
            ),
        )
        for action in merged.values()
    )
