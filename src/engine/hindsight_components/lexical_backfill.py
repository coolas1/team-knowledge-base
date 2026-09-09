"""Resumable lexical-token backfill for indexed Hindsight keyword retrieval."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import bindparam, func, select, update

from .models import MemoryUnit
from .utils import lexical_tokens


@dataclass(frozen=True, slots=True)
class LexicalBackfillReport:
    updated: int
    remaining: int
    complete: bool
    dry_run: bool = False


async def lexical_index_status(
    session_factory, *, document_id: str | None = None
) -> tuple[int, int]:
    """Return active row count and active rows that still need tokenization."""
    conditions = [MemoryUnit.state == "active"]
    if document_id is not None:
        import uuid

        conditions.append(MemoryUnit.document_id == uuid.UUID(document_id))
    async with session_factory() as session:
        total = int(
            await session.scalar(
                select(func.count()).select_from(MemoryUnit).where(*conditions)
            )
            or 0
        )
        remaining = int(
            await session.scalar(
                select(func.count())
                .select_from(MemoryUnit)
                .where(*conditions, MemoryUnit.lexical_tokens.is_(None))
            )
            or 0
        )
    return total, remaining


async def run_lexical_backfill(
    session_factory,
    *,
    batch_size: int = 500,
    dry_run: bool = False,
    document_id: str | None = None,
) -> LexicalBackfillReport:
    if batch_size < 1:
        raise ValueError("batch_size must be greater than zero")
    _, initial_remaining = await lexical_index_status(
        session_factory, document_id=document_id
    )
    if dry_run:
        return LexicalBackfillReport(
            updated=0,
            remaining=initial_remaining,
            complete=initial_remaining == 0,
            dry_run=True,
        )

    updated_count = 0
    import uuid

    document_condition = (
        [MemoryUnit.document_id == uuid.UUID(document_id)] if document_id else []
    )
    while True:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(MemoryUnit.id, MemoryUnit.text)
                        .where(MemoryUnit.lexical_tokens.is_(None), *document_condition)
                        .order_by(MemoryUnit.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if not rows:
                break
            statement = (
                update(MemoryUnit.__table__)
                .where(MemoryUnit.id == bindparam("memory_id"))
                .values(lexical_tokens=bindparam("tokens"))
            )
            await session.execute(
                statement,
                [
                    {"memory_id": memory_id, "tokens": lexical_tokens(memory_text)}
                    for memory_id, memory_text in rows
                ],
            )
            await session.commit()
            updated_count += len(rows)
    _, remaining = await lexical_index_status(session_factory, document_id=document_id)
    return LexicalBackfillReport(
        updated=updated_count,
        remaining=remaining,
        complete=remaining == 0,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hindsight-lexical-backfill",
        description="Backfill and validate memory_units.lexical_tokens.",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--document-id")
    return parser


async def _run(args: argparse.Namespace) -> int:
    from src.engine.components.store.postgres import async_session_factory, init_db

    await init_db()
    report = await run_lexical_backfill(
        async_session_factory,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        document_id=args.document_id,
    )
    print(json.dumps(asdict(report), ensure_ascii=False), flush=True)
    return 0 if report.complete or report.dry_run else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (ValueError, RuntimeError) as error:
        payload: dict[str, Any] = {"error": type(error).__name__}
        print(json.dumps(payload), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
