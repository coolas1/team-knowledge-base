"""Queue current PostgreSQL memories for rebuilding the Neo4j projection."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict, dataclass

from sqlalchemy import select

from .graph_outbox import GraphProjectionWorker, PostgresGraphOutbox
from .graph_projector import MemoryGraphProjector
from .models import HindsightDocumentState, HindsightGraphOutbox
from .neo4j_graph import HindsightNeo4jGraphStore
from .repository import PostgresMemoryRepository


@dataclass(frozen=True, slots=True)
class GraphBackfillReport:
    selected: int
    queued: int
    skipped: int
    dry_run: bool


class PostgresGraphBackfill:
    """Create document-level replace events without rerunning Retain or an LLM."""

    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def enqueue(
        self,
        *,
        document_id: str | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> GraphBackfillReport:
        uid = uuid.UUID(document_id) if document_id is not None else None
        async with self._session_factory() as session:
            async with session.begin():
                statement = (
                    select(HindsightDocumentState.document_id)
                    .where(HindsightDocumentState.status == "indexed")
                    .order_by(HindsightDocumentState.document_id)
                    .with_for_update()
                )
                if uid is not None:
                    statement = statement.where(
                        HindsightDocumentState.document_id == uid
                    )
                document_ids = list(await session.scalars(statement))
                queued_ids = set()
                if document_ids and not force:
                    queued_ids = set(
                        await session.scalars(
                            select(HindsightGraphOutbox.document_id)
                            .where(
                                HindsightGraphOutbox.document_id.in_(document_ids),
                                HindsightGraphOutbox.operation == "replace",
                                HindsightGraphOutbox.status.in_(
                                    ("pending", "processing", "failed")
                                ),
                            )
                            .distinct()
                        )
                    )
                candidates = [
                    item for item in document_ids if force or item not in queued_ids
                ]
                if not dry_run:
                    session.add_all(
                        HindsightGraphOutbox(
                            document_id=item,
                            operation="replace",
                        )
                        for item in candidates
                    )

        return GraphBackfillReport(
            selected=len(document_ids),
            queued=len(candidates),
            skipped=len(document_ids) - len(candidates),
            dry_run=dry_run,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hindsight-graph-backfill",
        description=(
            "Queue existing Hindsight memories for Neo4j without rerunning Retain."
        ),
    )
    parser.add_argument("--document-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--drain",
        action="store_true",
        help="Project queued events immediately instead of waiting for the web worker.",
    )
    parser.add_argument("--limit", type=int, default=1000)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if args.document_id is not None:
        uuid.UUID(args.document_id)
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    if args.dry_run and args.drain:
        raise ValueError("--dry-run cannot be combined with --drain")

    from src.engine.components.store.postgres import init_db

    await init_db()
    report = await PostgresGraphBackfill().enqueue(
        document_id=args.document_id,
        force=args.force,
        dry_run=args.dry_run,
    )
    output = {"event": "queued", **asdict(report)}

    if args.drain:
        store = HindsightNeo4jGraphStore()
        try:
            projector = MemoryGraphProjector(store)
            await projector.ensure_schema()
            results = await GraphProjectionWorker(
                PostgresGraphOutbox(),
                PostgresMemoryRepository(),
                projector,
            ).drain(limit=args.limit)
            output["projected"] = sum(item.status == "completed" for item in results)
            output["failed"] = sum(item.status == "failed" for item in results)
        finally:
            await store.close()

    print(json.dumps(output, ensure_ascii=False), flush=True)
    return 1 if output.get("failed") else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (ValueError, RuntimeError) as error:
        print(
            json.dumps({"event": "error", "error": str(error)}, ensure_ascii=False),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
