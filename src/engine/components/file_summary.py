"""Shared, persisted summary preparation for indexing and all retention entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select

from config.settings import settings
from src.engine.scope import MemoryScope, TagFilter
from .analyzer import Analyzer
from .store.file_summary import FileSummaryStore, SummaryIdentity
from .store.models import Document
from .store.scope import scope_predicate


@dataclass(frozen=True, slots=True)
class PreparedSummary:
    text: str
    identity: SummaryIdentity
    coverage: dict


class FileSummaryManager:
    def __init__(self, sessions, *, analyzer=None, scope: MemoryScope | None = None):
        self.sessions = sessions
        self.analyzer = analyzer or Analyzer()
        # None is reserved for the trusted indexing pipeline, which already owns
        # the document ID. Public retention always passes its authorized scope.
        self.scope = scope

    async def prepare(
        self, document_id: str, source: str, title: str
    ) -> PreparedSummary:
        async with self.sessions() as session:
            statement = select(Document).where(Document.id == uuid.UUID(document_id))
            if self.scope is not None:
                statement = statement.where(
                    scope_predicate(Document.bank_id, Document.tags, self.scope)
                )
            owner = await session.scalar(statement)
            if owner is None:
                raise ValueError("document is not visible")
            scope = self.scope or MemoryScope(
                bank_id=owner.bank_id,
                visibility=TagFilter(tuple(owner.tags or ()), "exact"),
            )
        model = settings.llm.require_model() if settings.llm.enabled else "extractive"
        identity = SummaryIdentity.for_text(source, title, model)
        store = FileSummaryStore(self.sessions, scope=scope)
        cached = await store.get(document_id, identity)
        if cached is not None:
            return PreparedSummary(cached.summary, identity, dict(cached.coverage))
        coverage = {
            "source_chars": len(source),
            "input_chars": min(len(source), identity.input_chars),
            "complete": len(source) <= identity.input_chars,
            "kind": "generated" if settings.llm.enabled else "extractive",
        }
        try:
            result = await self.analyzer.summarize_document(source, title)
            await store.save(
                document_id, identity, summary=result.overview, coverage=coverage
            )
        except Exception as error:
            await store.save(
                document_id,
                identity,
                summary="",
                coverage=coverage,
                error_code=type(error).__name__,
            )
            raise
        # Re-read the committed winner so concurrent completions agree on text.
        saved = await store.get(document_id, identity)
        if saved is None:
            raise ValueError("summary no longer visible")
        return PreparedSummary(saved.summary, identity, dict(saved.coverage))
