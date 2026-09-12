"""Additive PostgreSQL state; files are immutable per generation attempt."""

from datetime import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.engine.components.store.models import Base


class PPTJob(Base):
    __tablename__ = "ppt_jobs"
    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    bank_id: Mapped[str] = mapped_column(Text, index=True)
    authority: Mapped[str] = mapped_column(Text)
    binding: Mapped[dict] = mapped_column(JSONB)
    session_id: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        Text, default="awaiting_outline_approval", index=True
    )
    spec: Mapped[dict] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB)
    backend: Mapped[dict] = mapped_column(JSONB)
    approvals: Mapped[dict] = mapped_column(JSONB, default=dict)
    budget: Mapped[dict] = mapped_column(JSONB)
    accounting: Mapped[dict] = mapped_column(JSONB, default=dict)
    artifact: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PPTPage(Base):
    __tablename__ = "ppt_pages"
    job_id: Mapped[str] = mapped_column(
        ForeignKey("ppt_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    cache_key: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    repairs: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    qa: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


class PPTEvent(Base):
    __tablename__ = "ppt_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("ppt_jobs.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


async def migrate(engine):
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[PPTJob.__table__, PPTPage.__table__, PPTEvent.__table__],
                checkfirst=True,
            )
        )
