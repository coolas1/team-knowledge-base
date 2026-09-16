"""Add explicit, queryable lifecycle provenance to existing memory rows."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def migrate_memory_lifecycle(
    engine: AsyncEngine, *, schema: str = "public"
) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema identifier")
    table = f'"{schema}"."memory_units"'
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"tkb-memory-lifecycle-migration:{schema}"},
        )
        await connection.execute(
            text(
                f"ALTER TABLE {table} "
                "ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'unknown', "
                "ADD COLUMN IF NOT EXISTS authority TEXT NOT NULL DEFAULT 'unclassified', "
                "ADD COLUMN IF NOT EXISTS policy_version INTEGER NOT NULL DEFAULT 1, "
                "ADD COLUMN IF NOT EXISTS confirmed_by_turn_id TEXT, "
                "ADD COLUMN IF NOT EXISTS derived_from_evidence_ids TEXT[] NOT NULL DEFAULT '{}', "
                "ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ, "
                "ADD COLUMN IF NOT EXISTS lifecycle_state TEXT NOT NULL DEFAULT 'current', "
                "ADD COLUMN IF NOT EXISTS superseded_by UUID, "
                "ADD COLUMN IF NOT EXISTS lifecycle_key TEXT, "
                "ADD COLUMN IF NOT EXISTS content_fingerprint TEXT, "
                "ADD COLUMN IF NOT EXISTS duplicate_of UUID"
            )
        )
        # Recover safe legacy JSON values. Invalid or absent values retain the
        # conservative defaults instead of gaining authority during upgrade.
        await connection.execute(
            text(
                f"UPDATE {table} SET "
                "origin = COALESCE(NULLIF(metadata_json->>'origin', ''), origin), "
                "authority = COALESCE(NULLIF(metadata_json->>'authority', ''), authority), "
                "policy_version = CASE "
                "WHEN COALESCE(metadata_json->>'retention_policy_version', "
                "metadata_json->>'policy_version', '') ~ '^[1-9][0-9]*$' "
                "THEN COALESCE(metadata_json->>'retention_policy_version', "
                "metadata_json->>'policy_version')::INTEGER ELSE policy_version END, "
                "confirmed_by_turn_id = COALESCE(NULLIF(metadata_json->>'confirmed_by_turn_id', ''), "
                "confirmed_by_turn_id), "
                "derived_from_evidence_ids = CASE "
                "WHEN jsonb_typeof(metadata_json->'derived_from_evidence_ids') = 'array' "
                "THEN ARRAY(SELECT jsonb_array_elements_text(metadata_json->'derived_from_evidence_ids')) "
                "ELSE derived_from_evidence_ids END, "
                "lifecycle_state = CASE "
                "WHEN metadata_json->>'lifecycle_state' IN "
                "('current', 'superseded', 'expired', 'retired') "
                "THEN metadata_json->>'lifecycle_state' ELSE lifecycle_state END"
            )
        )
        # Older selective-retention writers labelled every user statement as
        # confirmed. Downgrade unverifiable rows before enforcing the invariant.
        await connection.execute(
            text(
                f"UPDATE {table} SET authority = 'user_stated', "
                "metadata_json = jsonb_set(metadata_json, '{authority}', "
                "'\"user_stated\"'::jsonb, true) "
                "WHERE authority = 'user_confirmed' "
                "AND confirmed_by_turn_id IS NULL"
            )
        )
        await connection.execute(
            text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS "
                "ck_memory_units_confirmed_authority, "
                "ADD CONSTRAINT ck_memory_units_confirmed_authority CHECK "
                "(authority <> 'user_confirmed' OR confirmed_by_turn_id IS NOT NULL)"
            )
        )
        await connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS idx_memory_units_lifecycle "
                f"ON {table} (bank_id, lifecycle_state)"
            )
        )
        await connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS idx_memory_units_expires_at "
                f"ON {table} (expires_at)"
            )
        )
        await connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS idx_memory_units_fingerprint "
                f"ON {table} (bank_id, content_fingerprint)"
            )
        )
        await connection.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS idx_memory_units_lifecycle_key "
                f"ON {table} (bank_id, lifecycle_key)"
            )
        )
