"""Preserve entity IDs while allowing contextually distinct equal names."""

import re
from sqlalchemy import text


async def migrate_entities(engine, *, schema: str = "public") -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema identifier")
    table = f'"{schema}"."memory_entities"'
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"tkb-entity-migration:{schema}"},
        )
        if (
            await conn.scalar(text("SELECT to_regclass(:table)"), {"table": table})
            is None
        ):
            return
        await conn.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS identity_key TEXT NOT NULL DEFAULT ''"
            )
        )
        if not await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(:table) AND conname='uq_memory_entities_bank_identity')"
            ),
            {"table": table},
        ):
            await conn.execute(
                text(
                    f"ALTER TABLE {table} ADD CONSTRAINT uq_memory_entities_bank_identity UNIQUE(bank_id, normalized_name, identity_key)"
                )
            )
        for legacy in (
            "uq_memory_entities_bank_name",
            "memory_entities_normalized_name_key",
        ):
            await conn.execute(
                text(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "{legacy}"')
            )
        await conn.execute(
            text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}"."memory_entity_corrections" (
                id UUID PRIMARY KEY, bank_id TEXT NOT NULL DEFAULT 'default-team'
                    REFERENCES "{schema}"."memory_banks"(id),
                source_entity_id UUID NOT NULL, target_entity_id UUID NOT NULL,
                memory_ids UUID[] NOT NULL, reason TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')
        )
        mentions = f'"{schema}"."memory_unit_entities"'
        if (
            await conn.scalar(text("SELECT to_regclass(:table)"), {"table": mentions})
            is not None
        ):
            await conn.execute(
                text(
                    f"ALTER TABLE {mentions} ADD COLUMN IF NOT EXISTS original_name TEXT NOT NULL DEFAULT ''"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {mentions} ADD COLUMN IF NOT EXISTS aliases TEXT[] NOT NULL DEFAULT '{{}}'::text[]"
                )
            )
            await conn.execute(
                text(
                    f"UPDATE {mentions} m SET original_name=e.canonical_name FROM {table} e WHERE m.entity_id=e.id AND m.original_name=''"
                )
            )
