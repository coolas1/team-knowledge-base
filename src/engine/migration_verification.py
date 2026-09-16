"""Post-migration gates and feature-off rollback for retrieval rollout."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import uuid

from sqlalchemy import func, select, text

from config.settings import settings
from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentRetrieval,
    MemoryBank,
)
from src.engine.conversation_cleanup import (
    ConversationCleanupManifest,
    RETIRE_CLASSES,
)
from src.engine.hindsight_components.models import (
    ConsolidationJob,
    HindsightGraphOutbox,
    MemoryUnit,
    MentalModelRefreshJob,
    RetrievalMigrationDocument,
    RetrievalMigrationRun,
)
from src.engine.scope import MemoryScope


class RetrievalMigrationVerifier:
    def __init__(self, sessions, *, scope: MemoryScope):
        self.sessions = sessions
        self.scope = scope

    async def inspect(
        self,
        run_id: str,
        *,
        embedding_model: str,
        cleanup_manifest: ConversationCleanupManifest | None = None,
    ) -> dict:
        identity = uuid.UUID(run_id)
        async with self.sessions() as session:
            run = await session.scalar(
                select(RetrievalMigrationRun).where(
                    RetrievalMigrationRun.id == identity,
                    RetrievalMigrationRun.bank_id == self.scope.bank_id,
                )
            )
            if run is None:
                raise ValueError("migration run is missing or outside scope")
            targets = list(
                await session.scalars(
                    select(RetrievalMigrationDocument).where(
                        RetrievalMigrationDocument.run_id == identity
                    )
                )
            )
            document_ids = [target.document_id for target in targets]
            documents = {
                row.id: row
                for row in await session.scalars(
                    select(Document).where(Document.id.in_(document_ids))
                )
            }
            parents = {
                row.doc_id: row
                for row in await session.scalars(
                    select(DocumentRetrieval).where(
                        DocumentRetrieval.doc_id.in_(document_ids)
                    )
                )
            }
            chunks = list(
                await session.scalars(
                    select(Chunk).where(Chunk.doc_id.in_(document_ids))
                )
            )
            lexical_missing = int(
                await session.scalar(
                    select(func.count())
                    .select_from(MemoryUnit)
                    .where(
                        MemoryUnit.document_id.in_(document_ids),
                        MemoryUnit.state == "active",
                        MemoryUnit.lexical_tokens.is_(None),
                    )
                )
                or 0
            )
            index_state = {
                name: bool(
                    await session.scalar(
                        text(
                            "SELECT COALESCE((SELECT indisvalid AND indisready "
                            "FROM pg_index WHERE indexrelid = to_regclass(:name)), false)"
                        ),
                        {"name": name},
                    )
                )
                for name in (
                    "idx_document_retrieval_embedding",
                    "idx_chunks_embedding",
                    "idx_memory_units_lexical_tokens",
                )
            }
            dependencies = {
                "graph": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(HindsightGraphOutbox)
                        .where(
                            HindsightGraphOutbox.bank_id == self.scope.bank_id,
                            HindsightGraphOutbox.status != "completed",
                        )
                    )
                    or 0
                ),
                "mental_models": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(MentalModelRefreshJob)
                        .where(
                            MentalModelRefreshJob.bank_id == self.scope.bank_id,
                            MentalModelRefreshJob.status != "completed",
                        )
                    )
                    or 0
                ),
                "consolidation": int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ConsolidationJob)
                        .where(
                            ConsolidationJob.bank_id == self.scope.bank_id,
                            ConsolidationJob.pending_through
                            > ConsolidationJob.processed_through,
                        )
                    )
                    or 0
                ),
            }
            bank = await session.get(MemoryBank, self.scope.bank_id)
            scope_switches = dict(bank.config or {}) if bank else {}
            cleanup = await self._cleanup_state(session, cleanup_manifest)

        chunk_complete = sum(
            row.embedding is not None and row.embedding_model == embedding_model
            for row in chunks
        )
        parent_complete = sum(
            bool(
                (document := documents.get(target.document_id))
                and (parent := parents.get(target.document_id))
                and document.is_current
                and document.version_number == target.expected_revision
                and document.processing_generation == target.expected_generation
                and parent.revision == document.version_number
                and parent.generation_state == "ready"
                and parent.embedding is not None
                and parent.embedding_model == embedding_model
            )
            for target in targets
        )
        document_checkpoints = sum(
            target.status in {"backfilled", "validated", "verified"}
            for target in targets
        )
        scope_enabled = bool(
            scope_switches.get("hierarchical_retrieval_enabled")
            and scope_switches.get("keyword_index_enabled")
        )
        process_enabled = bool(
            settings.hindsight_hierarchical_retrieval_enabled
            and settings.hindsight_keyword_index_enabled
        )
        checks = {
            "document_checkpoints": document_checkpoints == len(targets),
            "parents": parent_complete == len(targets),
            "chunks": chunk_complete == len(chunks),
            "lexical": lexical_missing == 0,
            "indexes": all(index_state.values()),
            "cleanup": cleanup["active_targets"] == 0
            and cleanup["protected_mismatches"] == 0
            and cleanup["protected_checksum_match"],
            "dependencies": not any(dependencies.values()),
            "scope_read_switch": scope_enabled,
            "process_read_switch": process_enabled,
        }
        return {
            "run_id": run_id,
            "stage": run.stage,
            "checks": checks,
            "passed": all(checks.values()),
            "counts": {
                "documents": len(targets),
                "complete_parents": parent_complete,
                "chunks": len(chunks),
                "complete_chunks": chunk_complete,
                "lexical_missing": lexical_missing,
            },
            "index_state": index_state,
            "dependency_queues": dependencies,
            "cleanup": cleanup,
            "read_switch": {
                "scope": scope_enabled,
                "process": process_enabled,
                "effective": scope_enabled and process_enabled,
            },
        }

    async def enable_reads(self, run_id: str, *, embedding_model: str) -> dict:
        report = await self.inspect(run_id, embedding_model=embedding_model)
        prerequisites = {
            key: value
            for key, value in report["checks"].items()
            if key not in {"scope_read_switch", "process_read_switch"}
        }
        if not all(prerequisites.values()):
            raise ValueError("migration completeness gates failed")
        async with self.sessions() as session, session.begin():
            run = await session.get(
                RetrievalMigrationRun, uuid.UUID(run_id), with_for_update=True
            )
            bank = await session.get(
                MemoryBank, self.scope.bank_id, with_for_update=True
            )
            if run is None or run.bank_id != self.scope.bank_id or bank is None:
                raise ValueError("migration run or bank is missing")
            bank.config = {
                **(bank.config or {}),
                "hierarchical_retrieval_enabled": True,
                "keyword_index_enabled": True,
                "retrieval_migration_generation": str(run.generation),
            }
            run.stage = "read_enabled"
            await session.execute(
                RetrievalMigrationDocument.__table__.update()
                .where(RetrievalMigrationDocument.run_id == run.id)
                .values(status="validated")
            )
        return await self.inspect(run_id, embedding_model=embedding_model)

    async def verify(self, run_id: str, *, embedding_model: str) -> dict:
        report = await self.inspect(run_id, embedding_model=embedding_model)
        if report["passed"]:
            async with self.sessions() as session, session.begin():
                run = await session.get(
                    RetrievalMigrationRun, uuid.UUID(run_id), with_for_update=True
                )
                run.stage = "verified"
                await session.execute(
                    RetrievalMigrationDocument.__table__.update()
                    .where(RetrievalMigrationDocument.run_id == run.id)
                    .values(status="verified")
                )
            report["stage"] = "verified"
        return report

    async def rollback_reads(self, run_id: str) -> dict:
        async with self.sessions() as session, session.begin():
            run = await session.get(
                RetrievalMigrationRun, uuid.UUID(run_id), with_for_update=True
            )
            bank = await session.get(
                MemoryBank, self.scope.bank_id, with_for_update=True
            )
            if run is None or run.bank_id != self.scope.bank_id or bank is None:
                raise ValueError("migration run or bank is missing")
            bank.config = {
                **(bank.config or {}),
                "hierarchical_retrieval_enabled": False,
                "keyword_index_enabled": False,
            }
            run.stage = "validated"
            run.lease_token = None
            run.lease_expires_at = None
        return {"run_id": run_id, "stage": "validated", "read_enabled": False}

    async def _cleanup_state(self, session, manifest):
        if manifest is None:
            empty_checksum = hashlib.sha256(b"[]").hexdigest()
            return {
                "active_targets": 0,
                "protected_mismatches": 0,
                "expected_protected_checksum": empty_checksum,
                "actual_protected_checksum": empty_checksum,
                "protected_checksum_match": True,
            }
        if manifest.bank_id != self.scope.bank_id:
            raise ValueError("cleanup manifest belongs to another bank")
        ids = [uuid.UUID(item.memory_id) for item in manifest.items]
        rows = {
            str(row.id): row
            for row in await session.scalars(
                select(MemoryUnit).where(
                    MemoryUnit.id.in_(ids), MemoryUnit.bank_id == self.scope.bank_id
                )
            )
        }
        targets = [
            item for item in manifest.items if item.classification in RETIRE_CLASSES
        ]
        protected = [
            item for item in manifest.items if item.classification not in RETIRE_CLASSES
        ]
        expected_protected = [
            {
                "id": item.memory_id,
                "state": item.state,
                "version": item.memory_version,
                "fingerprint": item.content_fingerprint,
            }
            for item in sorted(protected, key=lambda value: value.memory_id)
        ]
        actual_protected = [
            {
                "id": item.memory_id,
                "state": rows[item.memory_id].state,
                "version": rows[item.memory_id].memory_version,
                "fingerprint": rows[item.memory_id].content_fingerprint,
            }
            for item in sorted(protected, key=lambda value: value.memory_id)
            if item.memory_id in rows
        ]
        expected_checksum = hashlib.sha256(
            json.dumps(expected_protected, sort_keys=True).encode()
        ).hexdigest()
        actual_checksum = hashlib.sha256(
            json.dumps(actual_protected, sort_keys=True).encode()
        ).hexdigest()
        return {
            "active_targets": sum(
                rows.get(item.memory_id) is not None
                and rows[item.memory_id].state == "active"
                for item in targets
            ),
            "protected_mismatches": sum(
                rows.get(item.memory_id) is None
                or rows[item.memory_id].state != item.state
                or rows[item.memory_id].memory_version != item.memory_version
                or rows[item.memory_id].content_fingerprint != item.content_fingerprint
                for item in protected
            ),
            "expected_protected_checksum": expected_checksum,
            "actual_protected_checksum": actual_checksum,
            "protected_checksum_match": expected_checksum == actual_checksum,
        }


async def _run_cli(args) -> int:
    from src.engine.components.store.postgres import async_session_factory, init_db

    await init_db()
    verifier = RetrievalMigrationVerifier(
        async_session_factory, scope=MemoryScope(bank_id=args.bank)
    )
    if args.action == "rollback":
        report = await verifier.rollback_reads(args.run_id)
    elif args.action == "enable":
        report = await verifier.enable_reads(
            args.run_id, embedding_model=args.embedding_model
        )
    else:
        report = await verifier.verify(
            args.run_id, embedding_model=args.embedding_model
        )
    print(json.dumps(report, sort_keys=True))
    return 0 if args.action == "rollback" or report.get("passed") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("verify", "enable", "rollback"))
    parser.add_argument("--bank", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--embedding-model", default="")
    args = parser.parse_args(argv)
    if args.action != "rollback" and not args.embedding_model:
        parser.error("--embedding-model is required")
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
