"""One-time backfill: rebuild retrieval views for every current document.

Recomputes the metadata-prefixed retrieval view (title | filename |
overview[:300] + text) from stored ``chunk_text`` / memory text plus
``documents.overview`` — no original files needed, which also covers
documents whose upload files were lost — then re-embeds all chunks and
active memory units and rebuilds lexical tokens. Displayed text
(``chunk_text``, memory ``text``) is never modified.

Documents without filename or overview (conversation turns) keep their
plain-text vectors, matching the retain path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.engine.components.store.models import Chunk, Document
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
) -> dict[str, int]:
    """Re-embed chunks and active memories from retrieval views; return stats."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    conditions = [Document.is_current.is_(True), Document.status == "indexed"]
    if bank_id is not None:
        conditions.append(Document.bank_id == bank_id)
    if document_id is not None:
        conditions.append(Document.id == uuid.UUID(document_id))
    async with session_factory() as session:
        documents = list(
            await session.scalars(select(Document).where(*conditions))
        )
    stats = {"documents": 0, "chunks": 0, "memories": 0}
    for document in documents:
        filename = _filename_of(getattr(document, "file_path", None))
        overview = (getattr(document, "overview", None) or "").strip()
        if not (filename or overview):
            continue  # no clean metadata: plain-text vectors stay as retained
        prefix = retrieval_view_prefix(document.title, filename, overview)
        stats["documents"] += 1
        stats["chunks"] += await _reembed(
            session_factory,
            embed_batch,
            Chunk,
            Chunk.doc_id == document.id,
            prefix=prefix,
            batch_size=batch_size,
        )
        stats["memories"] += await _reembed(
            session_factory,
            embed_batch,
            MemoryUnit,
            MemoryUnit.document_id == document.id,
            MemoryUnit.state == "active",
            prefix=prefix,
            batch_size=batch_size,
            rebuild_tokens=True,
        )
        logger.info(
            "retrieval view rebuilt for %s (%s)", document.id, document.title
        )
    return stats


async def _reembed(
    session_factory: async_sessionmaker[AsyncSession],
    embed_batch: Any,
    model: type,
    *conditions: Any,
    prefix: str,
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
        vectors = await embed_batch([prefix + text for _id, text in batch])
        if len(vectors) != len(batch):
            raise ValueError("embedding provider returned an unexpected row count")
        async with session_factory() as session, session.begin():
            for (row_id, text), vector in zip(batch, vectors, strict=True):
                values: dict[str, Any] = {"embedding": vector}
                if rebuild_tokens:
                    values["lexical_tokens"] = lexical_tokens(prefix + text)
                await session.execute(
                    update(model).where(model.id == row_id).values(**values)
                )
    return len(rows)
