"""Durable orchestration state for scope-bounded retrieval migrations."""

from __future__ import annotations

import uuid
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    INTERNAL_DOCUMENT_FILE_TYPES,
)
from src.engine.components.store.scope import scope_predicate
from src.engine.hindsight_components.models import (
    RetrievalMigrationDocument,
    RetrievalMigrationRun,
    MemoryUnit,
)
from src.engine.hindsight_components.utils import lexical_tokens
from src.engine.retrieval_view import retrieval_view_prefix
from src.engine.scope import MemoryScope


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MigrationLease:
    run_id: str
    token: str
    generation: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BackfillOutcome:
    document_id: str | None
    status: str
    parent_embeddings: int = 0
    chunk_embeddings: int = 0


class RetrievalMigrationStore:
    """Own migration leases, retry state, fences, checkpoints, and budgets."""

    def __init__(self, sessions, *, scope: MemoryScope):
        self.sessions = sessions
        self.scope = scope

    async def dry_run_manifest(
        self,
        document_ids: list[str] | None = None,
        *,
        target_embedding_model: str,
    ) -> dict:
        """Describe required work and protected rows without mutating storage."""
        if not target_embedding_model.strip():
            raise ValueError("target_embedding_model is required")
        requested = (
            {uuid.UUID(value) for value in document_ids}
            if document_ids is not None
            else None
        )
        async with self.sessions() as session:
            visible = list(
                await session.scalars(
                    select(Document)
                    .where(
                        Document.is_current.is_(True),
                        scope_predicate(Document.bank_id, Document.tags, self.scope),
                    )
                    .order_by(Document.id)
                )
            )
            by_id = {document.id: document for document in visible}
            if requested is not None and requested - set(by_id):
                raise ValueError("migration documents are missing or outside scope")
            selected = [
                document
                for document in visible
                if document.file_type not in INTERNAL_DOCUMENT_FILE_TYPES
                and (requested is None or document.id in requested)
            ]
            selected_ids = {document.id for document in selected}
            parents = {
                row.doc_id: row
                for row in await session.scalars(
                    select(DocumentRetrieval).where(
                        DocumentRetrieval.doc_id.in_(selected_ids)
                    )
                )
            }
            chunks = list(
                await session.scalars(
                    select(Chunk).where(Chunk.doc_id.in_(selected_ids))
                )
            )
            memories = list(
                await session.scalars(
                    select(MemoryUnit).where(
                        MemoryUnit.document_id.in_(selected_ids),
                        MemoryUnit.state == "active",
                    )
                )
            )
            total_chunks = int(
                await session.scalar(select(func.count()).select_from(Chunk)) or 0
            )
            total_active_memories = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryUnit)
                    .where(MemoryUnit.state == "active")
                )
                or 0
            )

        chunks_by_document: dict[uuid.UUID, list] = {}
        memories_by_document: dict[uuid.UUID, list] = {}
        for row in chunks:
            chunks_by_document.setdefault(row.doc_id, []).append(row)
        for row in memories:
            memories_by_document.setdefault(row.document_id, []).append(row)

        documents = []
        for document in selected:
            parent = parents.get(document.id)
            document_chunks = chunks_by_document.get(document.id, [])
            document_memories = memories_by_document.get(document.id, [])
            parent_ready = bool(
                parent
                and parent.revision == document.version_number
                and parent.generation_state == "ready"
                and parent.embedding is not None
                and parent.embedding_model == target_embedding_model
            )
            vector_dimensions = sorted(
                {
                    len(row.embedding)
                    for row in document_chunks
                    if row.embedding is not None
                }
            )
            chunk_model_state: dict[str, int] = {}
            for row in document_chunks:
                model = row.embedding_model or "untracked"
                chunk_model_state[model] = chunk_model_state.get(model, 0) + 1
            chunks_to_embed = sum(
                row.embedding is None or row.embedding_model != target_embedding_model
                for row in document_chunks
            )
            lexical_complete = sum(
                row.lexical_tokens is not None for row in document_memories
            )
            # Legacy chunks do not carry model identity, so they are
            # conservatively included in estimated re-embedding work.
            documents.append(
                {
                    "document_id": str(document.id),
                    "revision": document.version_number,
                    "processing_generation": (
                        str(document.processing_generation)
                        if document.processing_generation
                        else None
                    ),
                    "parent": {
                        "present": parent is not None,
                        "ready": parent_ready,
                        "revision": parent.revision if parent else None,
                        "embedding_model": parent.embedding_model if parent else None,
                    },
                    "chunks": {
                        "total": len(document_chunks),
                        "with_vector": sum(
                            row.embedding is not None for row in document_chunks
                        ),
                        "vector_dimensions": vector_dimensions,
                        "model_state": chunk_model_state,
                    },
                    "lexical": {
                        "eligible": len(document_memories),
                        "complete": lexical_complete,
                        "missing": len(document_memories) - lexical_complete,
                    },
                    "estimated_embedding_work": {
                        "parents": int(not parent_ready),
                        "chunks": chunks_to_embed,
                        "estimated_tokens": sum(
                            row.token_count
                            if row.token_count is not None
                            else max(1, len(row.chunk_text) // 4)
                            for row in document_chunks
                        ),
                    },
                }
            )

        selected_chunk_count = len(chunks)
        selected_memory_count = len(memories)
        manifest = {
            "version": 1,
            "dry_run": True,
            "bank_id": self.scope.bank_id,
            "target_embedding_model": target_embedding_model,
            "documents": documents,
            "summary": {
                "documents": len(documents),
                "missing_or_stale_parents": sum(
                    not item["parent"]["ready"] for item in documents
                ),
                "chunks": selected_chunk_count,
                "chunk_vectors": sum(
                    item["chunks"]["with_vector"] for item in documents
                ),
                "lexical_eligible": selected_memory_count,
                "lexical_missing": sum(
                    item["lexical"]["missing"] for item in documents
                ),
                "estimated_parent_embeddings": sum(
                    item["estimated_embedding_work"]["parents"] for item in documents
                ),
                "estimated_chunk_embeddings": sum(
                    item["estimated_embedding_work"]["chunks"] for item in documents
                ),
                "estimated_tokens": sum(
                    item["estimated_embedding_work"]["estimated_tokens"]
                    for item in documents
                ),
            },
            "protected": {
                "current_documents": len(visible) - len(documents),
                "conversation_documents": sum(
                    document.file_type in INTERNAL_DOCUMENT_FILE_TYPES
                    for document in visible
                ),
                "chunks": total_chunks - selected_chunk_count,
                "active_memories": total_active_memories - selected_memory_count,
            },
        }
        manifest["checksum"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return manifest

    async def prepare(
        self,
        document_ids: list[str],
        *,
        token_limit: int = 1_000_000,
        cost_limit_usd: float = 0.0,
        max_attempts: int = 10,
    ) -> str:
        if token_limit < 1 or cost_limit_usd < 0 or max_attempts < 1:
            raise ValueError("migration limits must be positive")
        identities = [uuid.UUID(value) for value in dict.fromkeys(document_ids)]
        async with self.sessions() as session, session.begin():
            documents = list(
                await session.scalars(
                    select(Document)
                    .where(
                        Document.id.in_(identities),
                        Document.is_current.is_(True),
                        Document.status == "indexed",
                        Document.file_type.not_in(INTERNAL_DOCUMENT_FILE_TYPES),
                        scope_predicate(Document.bank_id, Document.tags, self.scope),
                    )
                    .order_by(Document.id)
                )
            )
            if len(documents) != len(identities):
                raise ValueError("migration documents are missing or outside scope")
            run = RetrievalMigrationRun(
                bank_id=self.scope.bank_id,
                token_limit=token_limit,
                cost_limit_usd=cost_limit_usd,
                max_attempts=max_attempts,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                RetrievalMigrationDocument(
                    run_id=run.id,
                    document_id=document.id,
                    expected_revision=document.version_number,
                    expected_generation=document.processing_generation,
                )
                for document in documents
            )
            return str(run.id)

    async def claim(
        self,
        run_id: str,
        *,
        lease_seconds: int = 300,
        now: datetime | None = None,
    ) -> MigrationLease | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or _utcnow()
        async with self.sessions() as session, session.begin():
            run = await self._run(session, run_id, lock=True)
            if run.stage in {"verified", "failed"}:
                return None
            if run.lease_expires_at is not None and run.lease_expires_at > claimed_at:
                return None
            if run.attempts >= run.max_attempts:
                run.stage = "failed"
                run.progress = {**run.progress, "last_error": "attempt_limit"}
                return None
            # A process interruption leaves its document in processing. The
            # expired scope lease is the authority to resume that checkpoint.
            await session.execute(
                update(RetrievalMigrationDocument)
                .where(
                    RetrievalMigrationDocument.run_id == run.id,
                    RetrievalMigrationDocument.status == "processing",
                )
                .values(status="pending", error_code="interrupted")
            )
            token = uuid.uuid4()
            expires_at = claimed_at + timedelta(seconds=lease_seconds)
            run.lease_token = token
            run.lease_expires_at = expires_at
            run.attempts += 1
            return MigrationLease(
                run_id=str(run.id),
                token=str(token),
                generation=str(run.generation),
                expires_at=expires_at,
            )

    async def next_document(self, lease: MigrationLease):
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            document = await session.scalar(
                select(RetrievalMigrationDocument)
                .where(
                    RetrievalMigrationDocument.run_id == run.id,
                    RetrievalMigrationDocument.status.in_(("pending", "failed")),
                    RetrievalMigrationDocument.attempts < run.max_attempts,
                )
                .order_by(RetrievalMigrationDocument.document_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if document is None:
                return None
            document.status = "processing"
            document.attempts += 1
            document.error_code = None
            return document

    async def checkpoint(
        self,
        lease: MigrationLease,
        document_id: str,
        status: str,
        **details,
    ) -> bool:
        if status not in {"backfilled", "validated", "verified"}:
            raise ValueError("invalid document checkpoint")
        identity = uuid.UUID(document_id)
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            target = await session.get(
                RetrievalMigrationDocument, (run.id, identity), with_for_update=True
            )
            current = await session.get(Document, identity)
            if target is None or target.status != "processing":
                raise ValueError("migration document is not claimed")
            if (
                current is None
                or not current.is_current
                or current.version_number != target.expected_revision
                or current.processing_generation != target.expected_generation
            ):
                target.status = "skipped_changed"
                target.error_code = "revision_or_generation_changed"
                return False
            target.status = status
            target.checkpoint = {**target.checkpoint, **details}
            return True

    async def fail_document(
        self, lease: MigrationLease, document_id: str, error_code: str
    ) -> None:
        if not error_code or len(error_code) > 80:
            raise ValueError("error_code must be a short sanitized identifier")
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            target = await session.get(
                RetrievalMigrationDocument,
                (run.id, uuid.UUID(document_id)),
                with_for_update=True,
            )
            if target is None or target.status != "processing":
                raise ValueError("migration document is not claimed")
            target.status = "failed"
            target.error_code = error_code

    async def charge(
        self, lease: MigrationLease, *, tokens: int, cost_usd: float = 0.0
    ) -> bool:
        if tokens < 0 or cost_usd < 0:
            raise ValueError("usage cannot be negative")
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            if run.tokens_used + tokens > run.token_limit or (
                run.cost_limit_usd and run.cost_used_usd + cost_usd > run.cost_limit_usd
            ):
                run.progress = {**run.progress, "budget_exhausted": True}
                return False
            run.tokens_used += tokens
            run.cost_used_usd += cost_usd
            return True

    async def release(self, lease: MigrationLease) -> None:
        async with self.sessions() as session, session.begin():
            run = await self._leased_run(session, lease, lock=True)
            run.lease_token = None
            run.lease_expires_at = None

    async def _run(self, session, run_id: str, *, lock: bool = False):
        statement = select(RetrievalMigrationRun).where(
            RetrievalMigrationRun.id == uuid.UUID(run_id),
            RetrievalMigrationRun.bank_id == self.scope.bank_id,
        )
        run = await session.scalar(statement.with_for_update() if lock else statement)
        if run is None:
            raise ValueError("migration run is missing or outside scope")
        return run

    async def _leased_run(self, session, lease: MigrationLease, *, lock: bool):
        run = await self._run(session, lease.run_id, lock=lock)
        if run.lease_token != uuid.UUID(lease.token):
            raise ValueError("migration lease is no longer owned")
        if run.generation != uuid.UUID(lease.generation):
            raise ValueError("migration generation changed")
        return run


class RetrievalBackfillWorker:
    """Embed outside transactions and atomically publish revision-fenced batches."""

    def __init__(
        self,
        store: RetrievalMigrationStore,
        embed_batch,
        *,
        embedding_model: str,
        batch_size: int = 32,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not embedding_model.strip():
            raise ValueError("embedding_model is required")
        self.store = store
        self.embed_batch = embed_batch
        self.embedding_model = embedding_model
        self.batch_size = batch_size

    async def process_next(self, lease: MigrationLease) -> BackfillOutcome:
        target = await self.store.next_document(lease)
        if target is None:
            return BackfillOutcome(None, "complete")
        document_id = str(target.document_id)
        async with self.store.sessions() as session:
            document = await session.get(Document, target.document_id)
            chunks = list(
                await session.scalars(
                    select(Chunk)
                    .where(Chunk.doc_id == target.document_id)
                    .order_by(Chunk.chunk_index, Chunk.id)
                )
            )
            if not self._matches(target, document):
                await self._mark_changed(lease, target.document_id)
                return BackfillOutcome(document_id, "skipped_changed")
            title = document.title
            filename = (
                document.file_path.rsplit("/", 1)[-1] if document.file_path else None
            )
            overview = document.overview or ""
            tags = list(document.tags or [])
            parent_text = retrieval_view_prefix(title, filename, overview).rstrip("\n")
            chunk_inputs = [(chunk.id, chunk.chunk_text) for chunk in chunks]

        estimated_tokens = max(1, len(parent_text) // 4) + sum(
            max(1, len(value) // 4) for _, value in chunk_inputs
        )
        if not await self.store.charge(lease, tokens=estimated_tokens):
            await self.store.fail_document(lease, document_id, "budget_exhausted")
            return BackfillOutcome(document_id, "budget_exhausted")

        parent_vectors = await self.embed_batch([parent_text])
        if len(parent_vectors) != 1:
            raise ValueError("embedding provider returned an unexpected row count")
        chunk_vectors = []
        for offset in range(0, len(chunk_inputs), self.batch_size):
            inputs = [
                value for _, value in chunk_inputs[offset : offset + self.batch_size]
            ]
            vectors = await self.embed_batch(inputs)
            if len(vectors) != len(inputs):
                raise ValueError("embedding provider returned an unexpected row count")
            chunk_vectors.extend(vectors)

        async with self.store.sessions() as session, session.begin():
            run = await self.store._leased_run(session, lease, lock=True)
            locked_target = await session.get(
                RetrievalMigrationDocument,
                (run.id, target.document_id),
                with_for_update=True,
            )
            current = await session.get(
                Document, target.document_id, with_for_update=True
            )
            if locked_target is None or locked_target.status != "processing":
                raise ValueError("migration document is not claimed")
            if not self._matches(locked_target, current):
                locked_target.status = "skipped_changed"
                locked_target.error_code = "revision_or_generation_changed"
                return BackfillOutcome(document_id, "skipped_changed")
            await session.execute(
                insert(DocumentRetrieval)
                .values(
                    doc_id=current.id,
                    bank_id=current.bank_id,
                    revision=current.version_number,
                    title=title,
                    filename=filename or "",
                    overview=overview,
                    tags=tags,
                    entities=[],
                    field_tokens=lexical_tokens(parent_text),
                    embedding=parent_vectors[0],
                    embedding_model=self.embedding_model,
                    generation_state="ready",
                )
                .on_conflict_do_update(
                    index_elements=[DocumentRetrieval.doc_id],
                    set_={
                        "bank_id": current.bank_id,
                        "revision": current.version_number,
                        "title": title,
                        "filename": filename or "",
                        "overview": overview,
                        "tags": tags,
                        "field_tokens": lexical_tokens(parent_text),
                        "embedding": parent_vectors[0],
                        "embedding_model": self.embedding_model,
                        "generation_state": "ready",
                    },
                )
            )
            for (chunk_id, _), vector in zip(chunk_inputs, chunk_vectors, strict=True):
                result = await session.execute(
                    update(Chunk)
                    .where(Chunk.id == chunk_id, Chunk.doc_id == current.id)
                    .values(embedding=vector, embedding_model=self.embedding_model)
                )
                if result.rowcount != 1:
                    raise ValueError("document chunks changed during migration")
            locked_target.status = "backfilled"
            locked_target.checkpoint = {
                "parent_embeddings": 1,
                "chunk_embeddings": len(chunk_inputs),
                "embedding_model": self.embedding_model,
            }
        return BackfillOutcome(document_id, "backfilled", 1, len(chunk_inputs))

    async def _mark_changed(
        self, lease: MigrationLease, document_id: uuid.UUID
    ) -> None:
        async with self.store.sessions() as session, session.begin():
            run = await self.store._leased_run(session, lease, lock=True)
            target = await session.get(
                RetrievalMigrationDocument,
                (run.id, document_id),
                with_for_update=True,
            )
            if target is not None:
                target.status = "skipped_changed"
                target.error_code = "revision_or_generation_changed"

    @staticmethod
    def _matches(target, document) -> bool:
        return bool(
            document is not None
            and document.is_current
            and document.status == "indexed"
            and document.version_number == target.expected_revision
            and document.processing_generation == target.expected_generation
        )
