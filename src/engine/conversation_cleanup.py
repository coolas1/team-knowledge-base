"""Conservative dry-run classification for historical conversation memories."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from src.engine.components.store.models import Document
from src.engine.components.store.scope import scope_predicate
from src.engine.hindsight_components.models import MemoryUnit
from src.engine.scope import MemoryScope

KNOWN_ORIGINS = frozenset(
    {"user", "assistant", "tool", "system", "migration", "historical_user"}
)
KNOWN_AUTHORITIES = frozenset(
    {
        "user_confirmed",
        "user_stated",
        "assistant_derived",
        "system_derived",
        "unconfirmed",
    }
)
RETIRE_CLASSES = frozenset(
    {"duplicate", "superseded", "expired", "disallowed_assistant_derived"}
)


@dataclass(frozen=True, slots=True)
class CleanupItem:
    memory_id: str
    document_id: str
    classification: str
    reason: str
    content_fingerprint: str | None
    duplicate_of: str | None
    superseded_by: str | None
    state: str
    lifecycle_state: str
    memory_version: int
    origin: str
    authority: str
    confirmed_by_turn_id: str | None
    derived_from_evidence_ids: tuple[str, ...]
    source_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConversationCleanupManifest:
    version: int
    bank_id: str
    reference_time: str
    items: tuple[CleanupItem, ...]
    counts: dict[str, int]
    checksum: str

    @property
    def unknown_blocks_retirement(self) -> bool:
        return self.counts.get("unknown", 0) > 0

    @property
    def retirement_ids(self) -> tuple[str, ...]:
        self.require_authorized()
        return tuple(
            item.memory_id
            for item in self.items
            if item.classification in RETIRE_CLASSES
        )

    def require_authorized(self) -> None:
        if self.unknown_blocks_retirement:
            raise ValueError("unknown provenance blocks automatic retirement")

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "bank_id": self.bank_id,
            "reference_time": self.reference_time,
            "items": [
                {
                    "memory_id": item.memory_id,
                    "document_id": item.document_id,
                    "classification": item.classification,
                    "reason": item.reason,
                    "content_fingerprint": item.content_fingerprint,
                    "duplicate_of": item.duplicate_of,
                    "superseded_by": item.superseded_by,
                    "state": item.state,
                    "lifecycle_state": item.lifecycle_state,
                    "memory_version": item.memory_version,
                    "origin": item.origin,
                    "authority": item.authority,
                    "confirmed_by_turn_id": item.confirmed_by_turn_id,
                    "derived_from_evidence_ids": list(item.derived_from_evidence_ids),
                    "source_memory_ids": list(item.source_memory_ids),
                }
                for item in self.items
            ],
            "counts": dict(self.counts),
            "checksum": self.checksum,
        }


def _known_provenance(row) -> bool:
    return row.origin in KNOWN_ORIGINS and row.authority in KNOWN_AUTHORITIES


def _winner(row) -> tuple[int, datetime, str]:
    authority = {"user_confirmed": 3, "user_stated": 2}.get(row.authority, 1)
    return authority, row.mentioned_at, str(row.id)


def classify_conversation_memories(
    rows: list,
    *,
    bank_id: str,
    reference_time: datetime | None = None,
) -> ConversationCleanupManifest:
    boundary = reference_time or datetime.now(timezone.utc)
    if boundary.tzinfo is None:
        raise ValueError("reference_time must include a timezone")
    duplicate_ids = {
        row.id
        for group in _fingerprint_groups(rows).values()
        for row in group
        if row is not max(group, key=_winner)
    }
    items = []
    for row in sorted(rows, key=lambda value: str(value.id)):
        duplicate_of = str(row.duplicate_of) if row.duplicate_of else None
        superseded_by = str(row.superseded_by) if row.superseded_by else None
        if not _known_provenance(row):
            classification, reason = "unknown", "provenance_unclassified"
        elif row.origin in {"assistant", "tool", "system"} and not (
            row.authority == "user_confirmed" and row.confirmed_by_turn_id
        ):
            classification, reason = (
                "disallowed_assistant_derived",
                "non_user_content_without_confirmation",
            )
        elif row.duplicate_of is not None or row.id in duplicate_ids:
            classification, reason = "duplicate", "semantic_identity_duplicate"
        elif row.superseded_by is not None or row.lifecycle_state == "superseded":
            classification, reason = "superseded", "newer_lifecycle_value"
        elif row.expires_at is not None and row.expires_at <= boundary:
            classification, reason = "expired", "retention_expiry_reached"
        else:
            classification, reason = "keep", "authoritative_current_memory"
        items.append(
            CleanupItem(
                memory_id=str(row.id),
                document_id=str(row.document_id),
                classification=classification,
                reason=reason,
                content_fingerprint=row.content_fingerprint,
                duplicate_of=duplicate_of,
                superseded_by=superseded_by,
                state=row.state,
                lifecycle_state=row.lifecycle_state,
                memory_version=row.memory_version,
                origin=row.origin,
                authority=row.authority,
                confirmed_by_turn_id=row.confirmed_by_turn_id,
                derived_from_evidence_ids=tuple(row.derived_from_evidence_ids or ()),
                source_memory_ids=tuple(str(value) for value in row.source_memory_ids),
            )
        )
    counts = {name: 0 for name in (*sorted(RETIRE_CLASSES), "keep", "unknown")}
    for item in items:
        counts[item.classification] += 1
    base = {
        "version": 1,
        "bank_id": bank_id,
        "reference_time": boundary.isoformat(),
        "items": [asdict(item) for item in items],
        "counts": counts,
    }
    checksum = hashlib.sha256(
        json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ConversationCleanupManifest(
        version=1,
        bank_id=bank_id,
        reference_time=boundary.isoformat(),
        items=tuple(items),
        counts=counts,
        checksum=checksum,
    )


def _fingerprint_groups(rows: list) -> dict[str, list]:
    groups: dict[str, list] = {}
    for row in rows:
        if row.content_fingerprint and _known_provenance(row):
            groups.setdefault(row.content_fingerprint, []).append(row)
    return {key: value for key, value in groups.items() if len(value) > 1}


class ConversationCleanupPlanner:
    def __init__(self, sessions, *, scope: MemoryScope):
        self.sessions = sessions
        self.scope = scope

    async def build_manifest(
        self, *, reference_time: datetime | None = None
    ) -> ConversationCleanupManifest:
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(MemoryUnit)
                    .join(Document, Document.id == MemoryUnit.document_id)
                    .where(
                        MemoryUnit.state == "active",
                        Document.file_type == "conversation",
                        Document.is_current.is_(True),
                        scope_predicate(Document.bank_id, Document.tags, self.scope),
                        MemoryUnit.bank_id == self.scope.bank_id,
                    )
                    .order_by(MemoryUnit.id)
                )
            )
        return classify_conversation_memories(
            rows, bank_id=self.scope.bank_id, reference_time=reference_time
        )


def export_cleanup_manifest(manifest: ConversationCleanupManifest, path: Path) -> None:
    """Create an immutable rollback artifact without conversation source text."""
    with path.open("x", encoding="utf-8") as output:
        json.dump(manifest.as_dict(), output, ensure_ascii=False, sort_keys=True)


def load_cleanup_manifest(
    path: Path, *, expected_bank_id: str
) -> ConversationCleanupManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    checksum = payload.pop("checksum", None)
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if checksum != expected:
        raise ValueError("cleanup manifest checksum mismatch")
    if payload.get("version") != 1 or payload.get("bank_id") != expected_bank_id:
        raise ValueError("cleanup manifest identity mismatch")
    allowed = {field.name for field in CleanupItem.__dataclass_fields__.values()}
    items = []
    for raw in payload.get("items", []):
        if set(raw) != allowed:
            raise ValueError("cleanup manifest item schema mismatch")
        items.append(
            CleanupItem(
                **{
                    **raw,
                    "derived_from_evidence_ids": tuple(
                        raw["derived_from_evidence_ids"]
                    ),
                    "source_memory_ids": tuple(raw["source_memory_ids"]),
                }
            )
        )
    return ConversationCleanupManifest(
        version=1,
        bank_id=payload["bank_id"],
        reference_time=payload["reference_time"],
        items=tuple(items),
        counts={str(key): int(value) for key, value in payload["counts"].items()},
        checksum=checksum,
    )


def validate_restoration_preconditions(
    manifest: ConversationCleanupManifest, rows: list
) -> None:
    """Reject restoration after any target identity or derivation has changed."""
    expected = {
        item.memory_id: item
        for item in manifest.items
        if item.classification in RETIRE_CLASSES
    }
    actual = {str(row.id): row for row in rows if str(row.id) in expected}
    if set(actual) != set(expected):
        raise ValueError("cleanup restoration target set changed")
    for identity, item in expected.items():
        row = actual[identity]
        unchanged = (
            row.state == "retired"
            and row.memory_version == item.memory_version + 1
            and row.content_fingerprint == item.content_fingerprint
            and row.lifecycle_state == item.lifecycle_state
            and (str(row.duplicate_of) if row.duplicate_of else None)
            == item.duplicate_of
            and (str(row.superseded_by) if row.superseded_by else None)
            == item.superseded_by
            and tuple(row.derived_from_evidence_ids or ())
            == item.derived_from_evidence_ids
            and tuple(str(value) for value in row.source_memory_ids)
            == item.source_memory_ids
        )
        if not unchanged:
            raise ValueError("cleanup restoration preconditions changed")
