"""Resumable backfill for parent records and original-text dense vectors.

Parent embeddings use clean title/filename/overview metadata. Chunk and memory
embeddings use original text; metadata-prefixed tokens remain available to the
lexical index. Displayed source text is never modified.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    INTERNAL_DOCUMENT_FILE_TYPES,
)
from src.engine.hindsight_components.models import MemoryUnit
from src.engine.hindsight_components.utils import lexical_tokens
from src.engine.retrieval_view import retrieval_view_prefix

logger = logging.getLogger(__name__)


def _filename_of(file_path: str | None) -> str | None:
    if not file_path:
        return None
    return file_path.rsplit("/", 1)[-1] or None


async def backfill_retrieval_views(
    session_factory: async_sessionmaker[AsyncSession],
    embed_batch: Any,
    *,
    bank_id: str | None = None,
    document_id: str | None = None,
    batch_size: int = 32,
    dry_run: bool = False,
) -> dict[str, int]:
    """Re-embed chunks and active memories from retrieval views; return stats."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    conditions = [
        Document.is_current.is_(True),
        Document.status == "indexed",
        Document.file_type.not_in(INTERNAL_DOCUMENT_FILE_TYPES),
    ]
    if bank_id is not None:
        conditions.append(Document.bank_id == bank_id)
    if document_id is not None:
        conditions.append(Document.id == uuid.UUID(document_id))
    async with session_factory() as session:
        documents = list(
            await session.scalars(select(Document).where(*conditions))
        )
    stats = {
        "documents": len(documents),
        "parent_records": 0,
        "chunks": 0,
        "memories": 0,
        "skipped_changed_revision": 0,
        "dry_run": int(dry_run),
    }
    logger.info(
        "retrieval.migration.start",
        extra={
            "migration_state": "dry_run" if dry_run else "executing",
            "scope_kind": "document" if document_id else "bank" if bank_id else "all",
            "document_count": len(documents),
            "batch_size": batch_size,
        },
    )
    for document in documents:
        filename = _filename_of(getattr(document, "file_path", None))
        overview = (getattr(document, "overview", None) or "").strip()
        prefix = retrieval_view_prefix(document.title, filename, overview)
        async with session_factory() as session:
            chunk_count = len(
                list(
                    await session.scalars(
                        select(Chunk.id).where(Chunk.doc_id == document.id)
                    )
                )
            )
            memory_count = len(
                list(
                    await session.scalars(
                        select(MemoryUnit.id).where(
                            MemoryUnit.document_id == document.id,
                            MemoryUnit.state == "active",
                        )
                    )
                )
            )
        if dry_run:
            stats["parent_records"] += 1
            stats["chunks"] += chunk_count
            stats["memories"] += memory_count
            continue
        parent_vectors = await embed_batch([prefix.rstrip("\n")])
        if len(parent_vectors) != 1:
            raise ValueError("embedding provider returned an unexpected row count")
        async with session_factory() as session, session.begin():
            current = await session.get(Document, document.id)
            if (
                current is None
                or current.version_number != document.version_number
                or not current.is_current
                or current.status != "indexed"
            ):
                stats["skipped_changed_revision"] += 1
                continue
            await session.execute(
                insert(DocumentRetrieval)
                .values(
                    doc_id=document.id,
                    bank_id=document.bank_id,
                    revision=document.version_number,
                    title=document.title,
                    filename=filename or "",
                    overview=overview,
                    tags=list(document.tags or []),
                    entities=[],
                    field_tokens=lexical_tokens(prefix),
                    embedding=parent_vectors[0],
                    embedding_model="backfill-runtime-model",
                    generation_state="ready",
                )
                .on_conflict_do_update(
                    index_elements=[DocumentRetrieval.doc_id],
                    set_={
                        "revision": document.version_number,
                        "title": document.title,
                        "filename": filename or "",
                        "overview": overview,
                        "tags": list(document.tags or []),
                        "field_tokens": lexical_tokens(prefix),
                        "embedding": parent_vectors[0],
                        "generation_state": "ready",
                    },
                )
            )
        stats["parent_records"] += 1
        stats["chunks"] += await _reembed(
            session_factory,
            embed_batch,
            Chunk,
            Chunk.doc_id == document.id,
            embedding_prefix="",
            token_prefix="",
            batch_size=batch_size,
        )
        stats["memories"] += await _reembed(
            session_factory,
            embed_batch,
            MemoryUnit,
            MemoryUnit.document_id == document.id,
            MemoryUnit.state == "active",
            embedding_prefix="",
            token_prefix=prefix,
            batch_size=batch_size,
            rebuild_tokens=True,
        )
        logger.info(
            "retrieval.migration.document.complete",
            extra={
                "migration_state": "ready",
                "revision": document.version_number,
                "chunk_count": chunk_count,
                "memory_count": memory_count,
            },
        )
    logger.info(
        "retrieval.migration.complete",
        extra={
            "migration_state": "dry_run_complete" if dry_run else "completed",
            "document_count": stats["documents"],
            "parent_record_count": stats["parent_records"],
            "chunk_count": stats["chunks"],
            "memory_count": stats["memories"],
            "skipped_changed_revision_count": stats["skipped_changed_revision"],
        },
    )
    return stats


async def _reembed(
    session_factory: async_sessionmaker[AsyncSession],
    embed_batch: Any,
    model: type,
    *conditions: Any,
    embedding_prefix: str,
    token_prefix: str,
    batch_size: int,
    rebuild_tokens: bool = False,
) -> int:
    id_column = model.id
    text_column = Chunk.chunk_text if model is Chunk else MemoryUnit.text
    async with session_factory() as session:
        rows = [
            (row_id, text)
            for row_id, text in (
                await session.execute(select(id_column, text_column).where(*conditions))
            )
        ]
    for offset in range(0, len(rows), batch_size):
        batch: Sequence[tuple[Any, str]] = rows[offset : offset + batch_size]
        vectors = await embed_batch([embedding_prefix + text for _id, text in batch])
        if len(vectors) != len(batch):
            raise ValueError("embedding provider returned an unexpected row count")
        async with session_factory() as session, session.begin():
            for (row_id, text), vector in zip(batch, vectors, strict=True):
                values: dict[str, Any] = {"embedding": vector}
                if rebuild_tokens:
                    values["lexical_tokens"] = lexical_tokens(token_prefix + text)
                await session.execute(
                    update(model).where(model.id == row_id).values(**values)
                )
    return len(rows)
