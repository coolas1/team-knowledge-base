"""Backfill Hindsight memories from already-indexed TKB documents.

This command reads ``documents.raw_text`` and calls Hindsight retain directly.
It deliberately does not invoke GraphRAG reindexing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Protocol

from sqlalchemy import or_, select

from src.engine.components.store.models import Document

from .models import HindsightDocumentState
from .providers import ProjectHindsightProviders
from .repository import PostgresMemoryRepository
from .service import HindsightService
from .types import RetainResult


@dataclass(frozen=True, slots=True)
class BackfillCandidate:
    document_id: str
    title: str
    content: str
    file_type: str
    hindsight_status: str | None = None


@dataclass(frozen=True, slots=True)
class BackfillItem:
    document_id: str
    title: str
    status: str
    memories: int = 0
    links: int = 0
    error: str | None = None


@dataclass(slots=True)
class BackfillReport:
    dry_run: bool
    selected: int
    succeeded: int = 0
    failed: int = 0
    items: list[BackfillItem] = field(default_factory=list)


class CandidateSource(Protocol):
    async def list_candidates(
        self,
        *,
        document_id: str | None = None,
        force: bool = False,
    ) -> list[BackfillCandidate]: ...


class StateStore(Protocol):
    async def set_document_state(
        self,
        document_id: str,
        status: str,
        *,
        error_msg: str | None = None,
    ) -> None: ...


class RetainService(Protocol):
    async def retain(self, **kwargs) -> RetainResult: ...


class PostgresCandidateSource:
    """Select eligible primary documents without changing GraphRAG state."""

    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from src.engine.components.store.postgres import async_session_factory

            session_factory = async_session_factory
        self._session_factory = session_factory

    async def list_candidates(
        self,
        *,
        document_id: str | None = None,
        force: bool = False,
    ) -> list[BackfillCandidate]:
        statement = (
            select(Document, HindsightDocumentState.status)
            .outerjoin(
                HindsightDocumentState,
                HindsightDocumentState.document_id == Document.id,
            )
            .where(Document.status == "indexed", Document.raw_text != "")
            .order_by(Document.created_at, Document.id)
        )
        if document_id is not None:
            statement = statement.where(Document.id == uuid.UUID(document_id))
        if not force:
            statement = statement.where(
                or_(
                    HindsightDocumentState.document_id.is_(None),
                    HindsightDocumentState.status != "indexed",
                )
            )

        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            BackfillCandidate(
                document_id=str(document.id),
                title=document.title,
                content=document.raw_text,
                file_type=document.file_type,
                hindsight_status=hindsight_status,
            )
            for document, hindsight_status in rows
        ]


async def run_backfill(
    source: CandidateSource,
    service: RetainService,
    state_store: StateStore,
    *,
    document_id: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    concurrency: int = 1,
    on_result: Callable[[BackfillItem], None] | None = None,
) -> BackfillReport:
    if concurrency < 1:
        raise ValueError("concurrency must be greater than zero")
    candidates = await source.list_candidates(document_id=document_id, force=force)
    if dry_run:
        return BackfillReport(
            dry_run=True,
            selected=len(candidates),
            items=[
                BackfillItem(
                    document_id=item.document_id,
                    title=item.title,
                    status="pending",
                )
                for item in candidates
            ],
        )

    semaphore = asyncio.Semaphore(concurrency)

    async def retain_one(candidate: BackfillCandidate) -> BackfillItem:
        async with semaphore:
            await state_store.set_document_state(
                candidate.document_id, "retaining", error_msg=None
            )
            try:
                result = await service.retain(
                    document_id=candidate.document_id,
                    title=candidate.title,
                    content=candidate.content,
                    file_type=candidate.file_type,
                    source_type="historical-backfill",
                )
                item = BackfillItem(
                    document_id=candidate.document_id,
                    title=candidate.title,
                    status="indexed",
                    memories=result.memories,
                    links=result.links,
                )
            except Exception as error:
                await state_store.set_document_state(
                    candidate.document_id, "failed", error_msg=str(error)
                )
                item = BackfillItem(
                    document_id=candidate.document_id,
                    title=candidate.title,
                    status="failed",
                    error=str(error),
                )
            if on_result is not None:
                on_result(item)
            return item

    items = list(await asyncio.gather(*(retain_one(item) for item in candidates)))
    return BackfillReport(
        dry_run=False,
        selected=len(items),
        succeeded=sum(item.status == "indexed" for item in items),
        failed=sum(item.status == "failed" for item in items),
        items=items,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hindsight-backfill",
        description="Build only Hindsight memories for existing indexed documents.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--document-id")
    parser.add_argument("--concurrency", type=int, default=1)
    return parser


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


async def _run(args: argparse.Namespace) -> int:
    if args.only_missing and args.force:
        raise ValueError("--only-missing and --force cannot be used together")
    if args.document_id is not None:
        uuid.UUID(args.document_id)

    from src.engine.components.store.postgres import init_db

    await init_db()
    repository = PostgresMemoryRepository()
    report = await run_backfill(
        PostgresCandidateSource(),
        HindsightService(repository, ProjectHindsightProviders()),
        repository,
        document_id=args.document_id,
        force=args.force,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        on_result=lambda item: _print_json({"event": "document", **asdict(item)}),
    )
    _print_json({"event": "summary", **asdict(report)})
    return 1 if report.failed else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (ValueError, RuntimeError) as error:
        _print_json({"event": "error", "error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
