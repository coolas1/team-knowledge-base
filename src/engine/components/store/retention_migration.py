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
        for job_table in ("consolidation_jobs", "mental_model_refresh_jobs"):
            qualified = f'"{schema}"."{job_table}"'
            if (
                await conn.scalar(
                    text("SELECT to_regclass(:name)"), {"name": qualified}
                )
                is not None
            ):
                await conn.execute(
                    text(
                        f"ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS operation_id "
                        "UUID NOT NULL DEFAULT gen_random_uuid()"
                    )
                )
        consolidation_table = f'"{schema}"."consolidation_jobs"'
        if (
            await conn.scalar(
                text("SELECT to_regclass(:name)"), {"name": consolidation_table}
            )
            is not None
        ):
            await conn.execute(
                text(
                    f"ALTER TABLE {consolidation_table} ADD COLUMN IF NOT EXISTS "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                )
            )
        # ``create_all`` does not add newly introduced tables on an existing
        # deployment. Create the B3 sidecar tables in dependency order here.
        from src.engine.hindsight_components.models import (
            ConsolidationFactEvent,
            ConsolidationJob,
            FactTombstone,
            MentalModelRefreshJob,
            MentalModelVersion,
            MemoryDirective,
            ObservationEvidence,
            ObservationHistory,
            ObservationRecord,
        )

        for model in (
            ObservationRecord,
            ObservationHistory,
            ObservationEvidence,
            FactTombstone,
            ConsolidationFactEvent,
            ConsolidationJob,
            MentalModelVersion,
            MentalModelRefreshJob,
            MemoryDirective,
        ):
            await conn.run_sync(
                lambda sync_conn, table=model.__table__: table.create(
                    sync_conn.execution_options(schema_translate_map={None: schema}),
                    checkfirst=True,
                )
            )
        await conn.execute(
            text(
                f'ALTER TABLE "{schema}"."consolidation_jobs" '
                "ADD COLUMN IF NOT EXISTS cost_microusd BIGINT NOT NULL DEFAULT 0"
            )
        )
        mental_table = f'"{schema}"."mental_models"'
        if (
            await conn.scalar(text("SELECT to_regclass(:name)"), {"name": mental_table})
            is not None
        ):
            additions = (
                "source_query TEXT NOT NULL DEFAULT ''",
                "version INTEGER NOT NULL DEFAULT 0",
                "refresh_mode TEXT NOT NULL DEFAULT 'full'",
                "refresh_after_consolidation BOOLEAN NOT NULL DEFAULT false",
                "refresh_interval_seconds INTEGER",
                "next_refresh_at TIMESTAMPTZ",
                "last_success_at TIMESTAMPTZ",
                "freshness TEXT NOT NULL DEFAULT 'empty'",
                "error_msg TEXT",
                "evidence_watermark BIGINT NOT NULL DEFAULT 0",
                "source_versions JSONB NOT NULL DEFAULT '{}'::jsonb",
            )
            for definition in additions:
                column = definition.split(" ", 1)[0]
                await conn.execute(
                    text(
                        f"ALTER TABLE {mental_table} ADD COLUMN IF NOT EXISTS {column} {definition.split(' ', 1)[1]}"
                    )
                )
            # Keep the additive schema writable by the immediately preceding
            # model, whose INSERT statement does not include source_query.
            await conn.execute(
                text(f"ALTER TABLE {mental_table} ALTER COLUMN source_query SET DEFAULT ''")
            )
            await conn.execute(
                text(
                    f"UPDATE {mental_table} SET source_query=description, "
                    "version=CASE WHEN summary='' THEN 0 ELSE 1 END, "
                    "freshness=CASE WHEN summary='' THEN 'empty' ELSE 'active' END, "
                    "last_success_at=CASE WHEN summary='' THEN NULL ELSE updated_at END "
                    "WHERE source_query=''"
                )
            )
            await conn.execute(
                text(
                    f'''INSERT INTO "{schema}"."memory_directives"
                    (id, bank_id, name, content, trigger, priority, is_active, tags,
                     created_at, updated_at)
                    SELECT id, bank_id, name,
                           CASE WHEN summary='' THEN description ELSE summary END,
                           trigger, 0, true, tags, created_at, updated_at
                    FROM {mental_table}
                    WHERE is_directive=true
                    ON CONFLICT (id, bank_id) DO NOTHING'''
                )
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
        await conn.execute(
            text(
                f"ALTER TABLE {memory_table} ADD COLUMN IF NOT EXISTS memory_version INTEGER NOT NULL DEFAULT 1"
            )
        )
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
        # A btree cannot store arbitrarily long observation text. Older
        # deployments indexed the full value and fail while backfilling a long
        # legacy observation. Replace that definition once with a compact hash;
        # callers retain a full-text equality predicate to guard collisions.
        observation_index = f'"{schema}"."idx_observation_exact"'
        index_definition = await conn.scalar(
            text("SELECT pg_get_indexdef(to_regclass(:name))"),
            {"name": observation_index},
        )
        if index_definition and "md5(normalized_text)" not in index_definition.replace(
            '"', ""
        ):
            await conn.execute(text(f"DROP INDEX {observation_index}"))
        await conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS "idx_observation_exact" '
                f'ON "{schema}"."observation_records" '
                "(bank_id, md5(normalized_text))"
            )
        )
        state_exists = (
            await conn.scalar(
                text("SELECT to_regclass(:name)"), {"name": state_table}
            )
            is not None
        )
        if state_exists:
            # The event backfill below reads revision, so this additive column
            # must exist before the backfill runs on a pre-B2 deployment.
            await conn.execute(
                text(
                    f"ALTER TABLE {state_table} ADD COLUMN IF NOT EXISTS "
                    "revision INTEGER NOT NULL DEFAULT 0"
                )
            )
        # Legacy one-shot observations become version 1 heads with queryable
        # evidence. Existing atomic facts enter the default write-scope queue;
        # the unique event constraint makes an interrupted migration resumable.
        await conn.execute(
            text(f'''
                INSERT INTO "{schema}"."observation_records"
                    (memory_id, bank_id, version, normalized_text, write_scope,
                     freshness, has_conflict, processed_through)
                SELECT id, bank_id, 1, regexp_replace(trim(text), '\\s+', ' ', 'g'),
                       scope_tags, CASE WHEN state='active' THEN 'active' ELSE 'stale' END,
                       false, 0
                FROM {memory_table}
                WHERE memory_type='observation'
                ON CONFLICT (memory_id) DO NOTHING
            ''')
        )
        await conn.execute(
            text(f'''
                INSERT INTO "{schema}"."observation_evidence"
                    (observation_id, fact_id, fact_version, bank_id, active)
                SELECT observation.id, source_id, COALESCE(fact.memory_version, 1),
                       observation.bank_id, fact.id IS NOT NULL AND fact.state='active'
                FROM {memory_table} observation
                CROSS JOIN LATERAL unnest(observation.source_memory_ids) source_id
                LEFT JOIN {memory_table} fact ON fact.id=source_id
                WHERE observation.memory_type='observation'
                ON CONFLICT (observation_id, fact_id) DO NOTHING
            ''')
        )
        await conn.execute(
            text(f'''
                INSERT INTO "{schema}"."consolidation_fact_events"
                    (bank_id, scope_key, write_scope, fact_id, fact_version,
                     document_id, document_revision, operation)
                SELECT memory.bank_id, '[]', ARRAY[]::text[], memory.id,
                       memory.memory_version, memory.document_id,
                       COALESCE(state.revision, 1), 'upsert'
                FROM {memory_table} memory
                LEFT JOIN "{schema}"."hindsight_document_state" state
                  ON state.document_id=memory.document_id
                WHERE memory.memory_type IN ('world', 'experience')
                  AND NOT memory.is_source_chunk AND memory.state='active'
                ON CONFLICT (bank_id, scope_key, fact_id, fact_version, operation)
                DO NOTHING
            ''')
        )
        await conn.execute(
            text(f'''
                INSERT INTO "{schema}"."consolidation_jobs"
                    (bank_id, scope_key, write_scope, status, pending_through,
                     processed_through, attempts, iterations, tokens_used,
                     cost_microusd, available_at)
                SELECT bank_id, scope_key, ARRAY[]::text[], 'pending', max(id),
                       0, 0, 0, 0, 0, now()
                FROM "{schema}"."consolidation_fact_events"
                GROUP BY bank_id, scope_key
                ON CONFLICT (bank_id, scope_key) DO UPDATE SET
                    pending_through=GREATEST(
                        "{schema}"."consolidation_jobs".pending_through,
                        EXCLUDED.pending_through
                    ),
                    status=CASE
                        WHEN "{schema}"."consolidation_jobs".processed_through < EXCLUDED.pending_through
                        THEN 'pending'
                        ELSE "{schema}"."consolidation_jobs".status
                    END,
                    available_at=LEAST(
                        "{schema}"."consolidation_jobs".available_at,
                        EXCLUDED.available_at
                    )
            ''')
        )
        if state_exists:
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
