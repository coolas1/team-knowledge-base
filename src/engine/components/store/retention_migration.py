"""Additive, repeatable queue lease and operation expansion."""

import re
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def migrate_retention(engine: AsyncEngine, *, schema: str = "public") -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema identifier")
    table = f'"{schema}"."conversation_memory_sources"'
    state_table = f'"{schema}"."hindsight_document_state"'
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"tkb-retention-migration:{schema}"},
        )
        await conn.execute(
            text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}"."retention_requests" (
                document_id UUID NOT NULL REFERENCES "{schema}"."documents"(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL, bank_id TEXT NOT NULL DEFAULT 'default-team'
                    REFERENCES "{schema}"."memory_banks"(id),
                request_hash TEXT NOT NULL, result_payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(document_id, request_id)
            )
        ''')
        )
        memory_table = f'"{schema}"."memory_units"'
        immediate = await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(:table) AND conname='uq_memory_source_index' AND NOT condeferrable)"
            ),
            {"table": memory_table},
        )
        if immediate:
            await conn.execute(
                text(
                    f"ALTER TABLE {memory_table} DROP CONSTRAINT uq_memory_source_index, ADD CONSTRAINT uq_memory_source_index UNIQUE(document_id, chunk_index, memory_index) DEFERRABLE INITIALLY DEFERRED"
                )
            )
        if (
            await conn.scalar(text("SELECT to_regclass(:name)"), {"name": state_table})
            is not None
        ):
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS content_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS extraction_cache JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS source_context JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS stage_results JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS operation_id UUID"
                )
            )
            await conn.execute(
                text(
                    f"UPDATE {state_table} SET operation_id=document_id WHERE operation_id IS NULL"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ALTER COLUMN operation_id SET NOT NULL"
                )
            )
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ALTER COLUMN operation_id SET DEFAULT gen_random_uuid()"
                )
            )
        if (
            await conn.scalar(text("SELECT to_regclass(:name)"), {"name": table})
            is None
        ):
            return
        await conn.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS source_context JSONB NOT NULL DEFAULT '{{}}'::jsonb"
            )
        )
        await conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS operation_id UUID")
        )
        await conn.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS stage_results JSONB NOT NULL DEFAULT '{{}}'::jsonb"
            )
        )
        await conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lease_token UUID")
        )
        await conn.execute(
            text(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                f"UPDATE {table} SET operation_id=document_id WHERE operation_id IS NULL"
            )
        )
        await conn.execute(
            text(f"ALTER TABLE {table} ALTER COLUMN operation_id SET NOT NULL")
        )
        await conn.execute(
            text(
                f"ALTER TABLE {table} ALTER COLUMN operation_id SET DEFAULT gen_random_uuid()"
            )
        )
        await conn.execute(
            text(
                f"UPDATE {table} SET stage_results=jsonb_build_object('delivery', 'accepted', 'retain', status) WHERE stage_results='{{}}'::jsonb"
            )
        )
