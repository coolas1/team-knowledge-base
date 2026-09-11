"""Add the summary sidecar to existing installations, without touching originals."""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .file_summary import FileSummary


async def migrate_file_summaries(
    engine: AsyncEngine, *, schema: str = "public"
) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("invalid schema identifier")
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"tkb-file-summary-migration:{schema}"},
        )
        await connection.run_sync(
            lambda sync: FileSummary.__table__.create(
                sync.execution_options(schema_translate_map={None: schema}),
                checkfirst=True,
            )
        )
