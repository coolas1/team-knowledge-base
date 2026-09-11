"""Versioned summary sidecar; legacy documents need no fabricated summary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, Document
from .scope import scope_predicate
from src.engine.scope import MemoryScope


@dataclass(frozen=True, slots=True)
class SummaryIdentity:
    source_hash: str
    title_hash: str
    model: str
    policy_version: str = "file-summary-v1"
    template_version: str = "bounded-overview-v2"
    input_chars: int = 24000
    output_chars: int = 2000

    def __post_init__(self) -> None:
        if not all(
            (
                self.source_hash,
                self.title_hash,
                self.model,
                self.policy_version,
                self.template_version,
            )
        ):
            raise ValueError("summary identity fields must be nonempty")
        if self.input_chars < 1 or self.output_chars < 1:
            raise ValueError("summary budgets must be positive")

    @classmethod
    def for_text(cls, source: str, title: str, model: str, **policy) -> SummaryIdentity:
        return cls(
            source_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            title_hash=hashlib.sha256(title.encode("utf-8")).hexdigest(),
            model=model,
            **policy,
        )

    @property
    def key(self) -> str:
        value = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FileSummary(Base):
    __tablename__ = "file_summaries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'failed')", name="ck_file_summary_status"
        ),
        CheckConstraint(
            "status <> 'success' OR length(btrim(summary)) > 0",
            name="ck_file_summary_nonempty",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    identity_key: Mapped[str] = mapped_column(Text, primary_key=True)
    identity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class FileSummaryStore:
    """Reads and writes inherit live document ownership, including tag changes."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        scope: MemoryScope | None = None,
    ) -> None:
        self.sessions = sessions
        self.scope = scope or MemoryScope()

    def with_scope(self, scope: MemoryScope) -> FileSummaryStore:
        return FileSummaryStore(self.sessions, scope=scope)

    async def get(
        self, document_id: str, identity: SummaryIdentity
    ) -> FileSummary | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(FileSummary)
                .join(Document, Document.id == FileSummary.document_id)
                .where(
                    FileSummary.document_id == uuid.UUID(document_id),
                    FileSummary.identity_key == identity.key,
                    FileSummary.status == "success",
                    scope_predicate(Document.bank_id, Document.tags, self.scope),
                )
            )

    async def save(
        self,
        document_id: str,
        identity: SummaryIdentity,
        *,
        summary: str,
        coverage: dict,
        error_code: str | None = None,
    ) -> None:
        if error_code is None and not summary.strip():
            raise ValueError("successful summary cannot be empty")
        if len(summary) > identity.output_chars:
            raise ValueError("summary exceeds output budget")
        async with self.sessions() as session, session.begin():
            owner = await session.scalar(
                select(Document.id)
                .where(
                    Document.id == uuid.UUID(document_id),
                    scope_predicate(Document.bank_id, Document.tags, self.scope),
                )
                .with_for_update()
            )
            if owner is None:
                raise ValueError("document is not visible")
            values = dict(
                document_id=owner,
                identity_key=identity.key,
                identity=asdict(identity),
                summary=summary if error_code is None else "",
                coverage=coverage,
                status="success" if error_code is None else "failed",
                error_code=error_code,
            )
            statement = insert(FileSummary).values(**values)
            # A late failure must not overwrite another worker's successful result.
            statement = statement.on_conflict_do_update(
                index_elements=[FileSummary.document_id, FileSummary.identity_key],
                set_={**values, "updated_at": text("now()")},
                where=FileSummary.status != "success",
            )
            await session.execute(statement)
