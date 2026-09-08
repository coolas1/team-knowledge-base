"""Resumable ownership expansion for existing PostgreSQL installations.

Run against a dedicated test database before enabling scoped traffic. The
migration never reassigns non-null ownership or drops source data. Backfill
batches commit independently; restarting discovers remaining NULL rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

TABLES = (
    "documents",
    "chunks",
    "memory_units",
    "memory_entities",
    "memory_entity_corrections",
    "memory_unit_entities",
    "memory_links",
    "mental_models",
    "memory_profiles",
    "hindsight_document_state",
    "retention_requests",
    "conversation_memory_sources",
    "hindsight_graph_outbox",
)
# Parents precede children. Outbox is deliberately excluded: delete events must
# survive removal of their document, and existing events belong to default-team.
PARENTS = {
    "chunks": ("doc_id", "documents", "id"),
    "memory_units": ("document_id", "documents", "id"),
    "memory_unit_entities": ("memory_id", "memory_units", "id"),
    "memory_links": ("source_memory_id", "memory_units", "id"),
    "hindsight_document_state": ("document_id", "documents", "id"),
    "retention_requests": ("document_id", "documents", "id"),
    "conversation_memory_sources": ("document_id", "documents", "id"),
}


@dataclass
class ScopeMigrationResult:
    complete: bool = False
    counts_before: dict[str, int] = field(default_factory=dict)
    counts_after: dict[str, int] = field(default_factory=dict)
    backfilled: dict[str, int] = field(default_factory=dict)


async def migrate_scope(
    engine: AsyncEngine,
    *,
    schema: str = "public",
    batch_size: int = 1000,
    max_batches: int | None = None,
) -> ScopeMigrationResult:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema identifier")
    if batch_size < 1 or (max_batches is not None and max_batches < 1):
        raise ValueError("batch limits must be positive")

    def table(name: str) -> str:
        return f'"{schema}"."{name}"'

    report = ScopeMigrationResult()
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        lock = {"key": f"tkb-scope-migration:{schema}"}
        await conn.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), lock
        )
        try:
            existing = []
            for name in TABLES:
                present = await conn.scalar(
                    text("SELECT to_regclass(:name)"), {"name": table(name)}
                )
                if present is not None:
                    existing.append(name)
                    report.counts_before[name] = int(
                        await conn.scalar(text(f"SELECT count(*) FROM {table(name)}"))
                    )
            await conn.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS {table("memory_banks")} (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    policy_version INTEGER NOT NULL DEFAULT 1 CHECK (policy_version > 0),
                    config JSONB NOT NULL DEFAULT '{{}}',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            )
            await conn.execute(
                text(f"""
                INSERT INTO {table("memory_banks")} (id, name)
                VALUES ('default-team', 'Shared team') ON CONFLICT (id) DO NOTHING
            """)
            )
            # Add nullable first: parent-derived values are populated explicitly
            # rather than filling all children with an incorrect default.
            for name in existing:
                await conn.execute(
                    text(
                        f"ALTER TABLE {table(name)} ADD COLUMN IF NOT EXISTS bank_id TEXT"
                    )
                )
                await conn.execute(
                    text(
                        f"ALTER TABLE {table(name)} ALTER COLUMN bank_id SET DEFAULT 'default-team'"
                    )
                )
            for name in ("documents", "chunks", "mental_models"):
                if name in existing:
                    await conn.execute(
                        text(
                            f"ALTER TABLE {table(name)} ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{{}}'"
                        )
                    )
            batches = 0
            for name in existing:
                report.backfilled[name] = 0
                parent = PARENTS.get(name)
                expression = "'default-team'"
                if parent:
                    fk, parent_name, pk = parent
                    expression = f"(SELECT p.bank_id FROM {table(parent_name)} p WHERE p.{pk} = target.{fk})"
                while True:
                    result = await conn.execute(
                        text(f"""
                        WITH batch AS (
                            SELECT ctid FROM {table(name)} WHERE bank_id IS NULL LIMIT :limit
                        )
                        UPDATE {table(name)} AS target SET bank_id = {expression}
                        FROM batch WHERE target.ctid = batch.ctid
                    """),
                        {"limit": batch_size},
                    )
                    updated = result.rowcount
                    if not updated:
                        break
                    # Orphans must fail visibly rather than loop over NULL rows.
                    if parent:
                        fk, parent_name, pk = parent
                        missing = await conn.scalar(
                            text(f"""
                            SELECT EXISTS (SELECT 1 FROM {table(name)} c
                            WHERE c.bank_id IS NULL AND NOT EXISTS (
                                SELECT 1 FROM {table(parent_name)} p WHERE p.{pk} = c.{fk}
                            ))
                        """)
                        )
                        if missing:
                            raise ValueError(
                                f"orphaned source in {name}; repair before migration"
                            )
                    report.backfilled[name] += updated
                    batches += 1
                    if max_batches is not None and batches >= max_batches:
                        return report
                await conn.execute(
                    text(f"ALTER TABLE {table(name)} ALTER COLUMN bank_id SET NOT NULL")
                )
                await conn.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS "idx_{name}_bank" ON {table(name)} (bank_id)'
                    )
                )

            if "memory_units" in existing:
                await conn.execute(
                    text(
                        f"ALTER TABLE {table('memory_units')} ADD COLUMN IF NOT EXISTS scope_tags TEXT[] NOT NULL DEFAULT '{{}}'"
                    )
                )
                while True:
                    result = await conn.execute(
                        text(f"""
                        WITH batch AS (
                            SELECT m.id, d.tags FROM {table("memory_units")} m
                            JOIN {table("documents")} d ON d.id=m.document_id
                            WHERE m.scope_tags IS DISTINCT FROM d.tags LIMIT :limit
                        )
                        UPDATE {table("memory_units")} m SET scope_tags=batch.tags
                        FROM batch WHERE m.id=batch.id
                    """),
                        {"limit": batch_size},
                    )
                    if not result.rowcount:
                        break
                    batches += 1
                    if max_batches is not None and batches >= max_batches:
                        return report

            async def constraint(
                name: str, constraint_name: str, definition: str
            ) -> None:
                found = await conn.scalar(
                    text("""
                    SELECT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = to_regclass(:table) AND conname = :name)
                """),
                    {"table": table(name), "name": constraint_name},
                )
                if not found:
                    await conn.execute(
                        text(
                            f'ALTER TABLE {table(name)} ADD CONSTRAINT "{constraint_name}" {definition}'
                        )
                    )

            for name in existing:
                await constraint(
                    name,
                    f"fk_{name}_bank",
                    f"FOREIGN KEY (bank_id) REFERENCES {table('memory_banks')}(id)",
                )
            for name in ("documents", "memory_units", "memory_entities"):
                if name in existing:
                    await constraint(name, f"uq_{name}_bank_id", "UNIQUE (bank_id, id)")
            for name, (fk, parent_name, pk) in PARENTS.items():
                if name in existing:
                    await constraint(
                        name,
                        f"fk_{name}_scope_source",
                        f"FOREIGN KEY (bank_id, {fk}) REFERENCES {table(parent_name)}(bank_id, {pk}) ON DELETE CASCADE",
                    )
            for name, fk, parent_name in (
                ("memory_links", "target_memory_id", "memory_units"),
                ("memory_unit_entities", "entity_id", "memory_entities"),
            ):
                if name in existing:
                    await constraint(
                        name,
                        f"fk_{name}_scope_target",
                        f"FOREIGN KEY (bank_id, {fk}) REFERENCES {table(parent_name)}(bank_id, id) ON DELETE CASCADE",
                    )
            # Install the replacement before dropping the legacy uniqueness rule.
            for name, cols, legacy, replacement in (
                (
                    "memory_entities",
                    "bank_id, normalized_name",
                    "memory_entities_normalized_name_key",
                    "uq_memory_entities_bank_name",
                ),
                (
                    "conversation_memory_sources",
                    "bank_id, session_id, turn_id",
                    "uq_conversation_memory_session_turn",
                    "uq_conversation_memory_bank_turn",
                ),
            ):
                if name in existing:
                    if name == "memory_entities" and await conn.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=:schema AND table_name='memory_entities' AND column_name='identity_key')"
                        ),
                        {"schema": schema},
                    ):
                        await constraint(
                            name,
                            "uq_memory_entities_bank_identity",
                            "UNIQUE (bank_id, normalized_name, identity_key)",
                        )
                        await conn.execute(
                            text(
                                f'ALTER TABLE {table(name)} DROP CONSTRAINT IF EXISTS "{legacy}"'
                            )
                        )
                        continue
                    await constraint(name, replacement, f"UNIQUE ({cols})")
                    await conn.execute(
                        text(
                            f'ALTER TABLE {table(name)} DROP CONSTRAINT IF EXISTS "{legacy}"'
                        )
                    )
            # Model names are user-controlled; equal IDs must work in two banks.
            for name in ("mental_models", "memory_profiles"):
                if name in existing:
                    current = await conn.scalar(
                        text("""
                        SELECT pg_get_constraintdef(oid) FROM pg_constraint
                        WHERE conrelid = to_regclass(:table) AND contype = 'p'
                    """),
                        {"table": table(name)},
                    )
                    if current == "PRIMARY KEY (id)":
                        # One statement: never expose a table without its PK.
                        await conn.execute(
                            text(
                                f'ALTER TABLE {table(name)} DROP CONSTRAINT "{name}_pkey", ADD PRIMARY KEY (id, bank_id)'
                            )
                        )
            for name in existing:
                report.counts_after[name] = int(
                    await conn.scalar(text(f"SELECT count(*) FROM {table(name)}"))
                )
            report.complete = True
            return report
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), lock
            )
