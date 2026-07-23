"""Verify the PostgreSQL source of truth and Neo4j team projection."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VENV = ROOT / ".venv" / "Lib" / "site-packages"
for path in (VENV, VENV / "win32", VENV / "win32" / "lib", VENV / "pythonwin"):
    sys.path.insert(0, str(path))
os.add_dll_directory(str(VENV / "pywin32_system32"))
sys.path.insert(0, str(ROOT))

from neo4j import AsyncGraphDatabase
from sqlalchemy import text

from src.db.config import settings
from src.db.postgres import engine


TEAMS = ("park-ops", "finance", "engineering")
GRAPH_NAMESPACES = (*TEAMS, "public")


async def main() -> None:
    result: dict[str, object] = {}
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                """
                SELECT d.team_id,
                       count(DISTINCT d.id) AS documents,
                       count(DISTINCT c.id) AS chunks,
                       count(DISTINCT e.id) AS entities,
                       count(DISTINCT o.id) FILTER (WHERE o.status = 'succeeded') AS succeeded_operations,
                       count(DISTINCT x.id) FILTER (WHERE x.processed_at IS NOT NULL) AS processed_outbox
                FROM documents d
                LEFT JOIN chunks c ON c.doc_id = d.id AND c.team_id = d.team_id
                LEFT JOIN extracted_entities e ON e.doc_id = d.id AND e.team_id = d.team_id
                LEFT JOIN operations o ON o.document_id = d.id AND o.team_id = d.team_id
                LEFT JOIN outbox_events x ON x.aggregate_id = d.id::text AND x.team_id = d.team_id
                WHERE d.team_id IN ('park-ops', 'finance', 'engineering')
                GROUP BY d.team_id
                ORDER BY d.team_id
                """
            )
        )
        result["postgres"] = [dict(row._mapping) for row in rows]

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    async with driver:
        async with driver.session() as session:
            counts = await session.run(
                """
                MATCH (n)
                WHERE n.team_id IN $teams
                RETURN n.team_id AS team_id,
                       count(n) AS nodes,
                       count(CASE WHEN n:Document THEN 1 END) AS documents,
                       count(CASE WHEN n:Entity THEN 1 END) AS entities
                ORDER BY team_id
                """,
                teams=list(GRAPH_NAMESPACES),
            )
            result["neo4j"] = await counts.data()
            cross = await session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE a.team_id IN $teams AND b.team_id IN $teams
                  AND a.team_id <> b.team_id
                RETURN count(r) AS cross_team_relationships
                """,
                teams=list(GRAPH_NAMESPACES),
            )
            result["neo4j_cross_team_relationships"] = (
                await cross.single()
            )["cross_team_relationships"]

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
