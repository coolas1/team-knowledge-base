"""Scoped, revision-checked retirement of legacy file evidence, with recovery."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from sqlalchemy import Text, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert
from sqlalchemy.orm import Mapped, mapped_column

from src.engine.components.store.models import Base, Document
from src.engine.scope import MemoryScope
from .models import (
    ConsolidationJob,
    HindsightDocumentState,
    MemoryUnit,
    MentalModel,
    MentalModelRefreshJob,
    ObservationEvidence,
    ObservationHistory,
    ObservationRecord,
)
from .repository import PostgresMemoryRepository
from .utils import document_lock_key


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


class FileRebuildRun(Base):
    __tablename__ = "file_memory_rebuild_runs"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    bank_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planned")
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    backup: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retired_fingerprint: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


_TABLES = {
    model.__tablename__: model
    for model in (
        MemoryUnit,
        ObservationRecord,
        ObservationEvidence,
        HindsightDocumentState,
        MentalModel,
        MentalModelRefreshJob,
    )
}


class FileMemoryRebuild:
    def __init__(self, sessions, *, scope: MemoryScope):
        self.sessions = sessions
        self.scope = scope
        self.repository = PostgresMemoryRepository(
            sessions, scope=scope, consolidation_enabled=True
        )

    async def preview(self, document_ids: list[str] | None = None) -> dict:
        """No mutation or generation; ambiguous provenance is never guessed."""
        async with self.sessions() as session:
            return await self._preview(session, document_ids)

    async def _preview(self, session, document_ids) -> dict:
        documents = list(
            await session.scalars(
                select(Document).where(self.repository._document_scope())
            )
        )
        by_id = {str(d.id): d for d in documents}
        requested = set(document_ids) if document_ids is not None else set(by_id)
        if requested - set(by_id):
            raise ValueError("requested document is missing or outside scope")
        units = list(
            await session.scalars(
                select(MemoryUnit).where(self.repository._memory_scope())
            )
        )
        states = {
            str(s.document_id): s
            for s in await session.scalars(
                select(HindsightDocumentState).where(
                    HindsightDocumentState.document_id.in_([d.id for d in documents])
                )
            )
        }
        targets, ambiguous, fact_ids = [], [], set()
        for identity in sorted(requested):
            document = by_id[identity]
            if document.file_type == "conversation":
                continue
            facts = [
                u
                for u in units
                if str(u.document_id) == identity
                and u.memory_type != "observation"
                and u.state == "active"
            ]
            legacy = [
                u for u in facts if not (u.metadata_json or {}).get("file_summary")
            ]
            if not legacy:
                continue
            if any(
                (u.metadata_json or {}).get("source_type")
                not in {"upload", "graphrag-pipeline", "historical-backfill"}
                for u in legacy
            ):
                ambiguous.append(identity)
                continue
            state = states.get(identity)
            targets.append(
                {
                    "document_id": identity,
                    "source_hash": digest(document.raw_text),
                    "title": document.title,
                    "tags": list(document.tags or ()),
                    "revision": state.revision if state else 0,
                    "fact_versions": {str(u.id): u.memory_version for u in legacy},
                }
            )
            fact_ids.update(u.id for u in legacy)
        edges = list(
            await session.scalars(
                select(ObservationEvidence).where(
                    ObservationEvidence.bank_id == self.scope.bank_id,
                    ObservationEvidence.active.is_(True),
                )
            )
        )
        affected = {e.observation_id for e in edges if e.fact_id in fact_ids}
        affected.update(
            u.id
            for u in units
            if u.memory_type == "observation"
            and u.state in {"active", "stale"}
            and fact_ids.intersection(u.source_memory_ids or ())
        )
        visible_units = {u.id: u for u in units}
        if affected - set(visible_units):
            raise ValueError(
                "dependent observations extend beyond migration visibility"
            )
        observations = []
        for identity in sorted(affected, key=str):
            unit = visible_units[identity]
            sources = {e.fact_id for e in edges if e.observation_id == identity}
            sources.update(unit.source_memory_ids or ())
            observations.append(
                {
                    "id": str(identity),
                    "document_id": str(unit.document_id),
                    "version": unit.memory_version,
                    "retained_fact_ids": sorted(str(i) for i in sources - fact_ids),
                }
            )
        graph_documents = {t["document_id"] for t in targets} | {
            o["document_id"] for o in observations
        }
        for observation in observations:
            graph_documents.update(
                str(visible_units[uuid.UUID(i)].document_id)
                for i in observation["retained_fact_ids"]
                if uuid.UUID(i) in visible_units
            )
        protected_ids = [
            u.id for u in units if u.id not in fact_ids and u.id not in affected
        ]
        protected_rows = list(
            await session.scalars(
                select(func.to_jsonb(MemoryUnit.__table__.table_valued())).where(
                    MemoryUnit.id.in_(protected_ids)
                )
            )
        )
        protected_rows = sorted(protected_rows, key=lambda row: row["id"])
        environment = dict(
            (
                await session.execute(
                    text(
                        "SELECT current_database() AS database, current_schema() AS schema, inet_server_addr()::text AS server, inet_server_port() AS port"
                    )
                )
            )
            .mappings()
            .one()
        )
        return {
            "protected_memory_ids": sorted(str(i) for i in protected_ids),
            "protected_memory_fingerprint": digest(protected_rows),
            "graph_document_ids": sorted(graph_documents),
            "environment": environment,
            "version": 1,
            "bank_id": self.scope.bank_id,
            "targets": targets,
            "observations": observations,
            "protected_observation_ids": sorted(
                str(u.id)
                for u in units
                if u.memory_type == "observation" and u.id not in affected
            ),
            "ambiguous_documents": ambiguous,
        }

    async def _lock_manifest(self, session, manifest):
        # Same scope advisory locks as consolidation publication, then the
        # same per-document locks as retention. No LLM runs in this transaction.
        scope_keys = set(
            await session.scalars(
                select(ConsolidationJob.scope_key).where(
                    ConsolidationJob.bank_id == self.scope.bank_id
                )
            )
        ) | {"[]"}
        for scope_key in sorted(scope_keys):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"tkb-consolidation:{self.scope.bank_id}:{scope_key}"},
            )
        document_ids = {t["document_id"] for t in manifest["targets"]}
        document_ids.update(o["document_id"] for o in manifest["observations"])
        for identity in sorted(document_ids):
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(document_lock_key(uuid.UUID(identity)))
                )
            )

    async def _snapshot(self, session, manifest):
        fact_ids = [
            uuid.UUID(i) for t in manifest["targets"] for i in t["fact_versions"]
        ]
        obs_ids = [uuid.UUID(o["id"]) for o in manifest["observations"]]
        doc_ids = [uuid.UUID(t["document_id"]) for t in manifest["targets"]]
        model_ids = list(
            await session.scalars(
                select(MentalModel.id).where(
                    MentalModel.bank_id == self.scope.bank_id,
                    MentalModel.source_memory_ids.overlap(fact_ids + obs_ids),
                )
            )
        )
        predicates = {
            MemoryUnit: MemoryUnit.id.in_(fact_ids + obs_ids),
            ObservationRecord: ObservationRecord.memory_id.in_(obs_ids),
            ObservationEvidence: ObservationEvidence.observation_id.in_(obs_ids),
            HindsightDocumentState: HindsightDocumentState.document_id.in_(doc_ids),
            MentalModel: MentalModel.id.in_(model_ids)
            & (MentalModel.bank_id == self.scope.bank_id),
            MentalModelRefreshJob: MentalModelRefreshJob.model_id.in_(model_ids)
            & (MentalModelRefreshJob.bank_id == self.scope.bank_id),
        }
        result = {}
        for model, predicate in predicates.items():
            # PostgreSQL's JSON encoding preserves vectors, UUIDs and timestamps
            # in the exact representation accepted by jsonb_populate_record.
            statement = select(func.to_jsonb(model.__table__.table_valued())).where(
                predicate
            )
            rows = list(await session.scalars(statement))
            result[model.__tablename__] = sorted(
                rows, key=lambda row: json.dumps(row, sort_keys=True)
            )
        return result

    async def create_run(self, manifest: dict) -> str:
        if manifest.get("bank_id") != self.scope.bank_id:
            raise ValueError("manifest belongs to another bank")
        if manifest.get("ambiguous_documents"):
            raise ValueError("resolve ambiguous file provenance before migration")
        async with self.sessions() as session, session.begin():
            await self._lock_manifest(session, manifest)
            current = await self._preview(
                session, [t["document_id"] for t in manifest["targets"]]
            )
            if digest(current) != digest(manifest):
                raise ValueError("manifest changed; replan")
            run = FileRebuildRun(
                bank_id=self.scope.bank_id,
                manifest=manifest,
                backup=await self._snapshot(session, manifest),
            )
            session.add(run)
            await session.flush()
            return str(run.id)

    async def get_run(self, run_id: str):
        async with self.sessions() as session:
            return await self._run(session, run_id)

    async def _run(self, session, run_id, *, lock=False):
        statement = select(FileRebuildRun).where(
            FileRebuildRun.id == uuid.UUID(run_id),
            FileRebuildRun.bank_id == self.scope.bank_id,
        )
        run = await session.scalar(statement.with_for_update() if lock else statement)
        if run is None:
            raise ValueError("migration run is not visible")
        # Runs cannot be used to widen a subsequently narrowed caller scope.
        for target in run.manifest["targets"]:
            if not self.scope.permits(run.bank_id, target["tags"]):
                raise ValueError("migration target is outside scope")
        document_ids = {t["document_id"] for t in run.manifest["targets"]} | {
            o["document_id"] for o in run.manifest["observations"]
        }
        document_ids.update(run.manifest.get("graph_document_ids", ()))
        visible = set(
            str(i)
            for i in await session.scalars(
                select(Document.id).where(
                    Document.id.in_([uuid.UUID(i) for i in document_ids]),
                    self.repository._document_scope(),
                )
            )
        )
        if visible != document_ids:
            raise ValueError("migration documents are no longer visible")
        return run

    async def export_backup(self, run_id: str, path: Path) -> None:
        run = await self.get_run(run_id)
        payload = {"run_id": run_id, "manifest": run.manifest, "backup": run.backup}
        with path.open("x", encoding="utf-8") as output:
            json.dump(
                {**payload, "checksum": digest(payload)}, output, ensure_ascii=False
            )
        async with self.sessions() as session, session.begin():
            current = await self._run(session, run_id, lock=True)
            current.progress = {**current.progress, "backup_checksum": digest(payload)}

    async def retire(self, run_id: str) -> dict:
        async with self.sessions() as session, session.begin():
            run = await self._run(session, run_id, lock=True)
            if run.status != "planned":
                return dict(run.progress)
            if not run.progress.get("backup_checksum"):
                raise ValueError("export recovery backup before retirement")
            await self._lock_manifest(session, run.manifest)
            current = await self._preview(
                session, [t["document_id"] for t in run.manifest["targets"]]
            )
            if digest(current) != digest(run.manifest) or digest(
                await self._snapshot(session, run.manifest)
            ) != digest(run.backup):
                raise ValueError("sources changed after backup; replan")
            fact_ids = [
                uuid.UUID(i)
                for t in run.manifest["targets"]
                for i in t["fact_versions"]
            ]
            obs_ids = [uuid.UUID(o["id"]) for o in run.manifest["observations"]]
            for identity in obs_ids:
                row = await session.get(MemoryUnit, identity)
                record = await session.get(ObservationRecord, identity)
                if record is not None:
                    history = (
                        insert(ObservationHistory)
                        .values(
                            bank_id=run.bank_id,
                            observation_id=identity,
                            version=record.version,
                            text=row.text,
                            freshness=record.freshness,
                            change_kind="file_rebuild",
                            reason="legacy file evidence retired",
                            evidence_snapshot=[
                                e
                                for e in run.backup["observation_evidence"]
                                if e["observation_id"] == str(identity)
                            ],
                        )
                        .on_conflict_do_nothing()
                    )
                    await session.execute(history)
                    record.version += 1
                    record.freshness = "tombstoned"
                    record.stale_reason = "file_rebuild"
                    record.normalized_text = ""
                row.text = ""
                row.source_text = ""
            await session.execute(
                update(MemoryUnit)
                .where(MemoryUnit.id.in_(fact_ids + obs_ids))
                .values(state="retired", memory_version=MemoryUnit.memory_version + 1)
            )
            await session.execute(
                update(ObservationEvidence)
                .where(ObservationEvidence.observation_id.in_(obs_ids))
                .values(active=False)
            )
            for target in run.manifest["targets"]:
                state = await session.get(
                    HindsightDocumentState, uuid.UUID(target["document_id"])
                )
                if state is None:
                    state = HindsightDocumentState(
                        document_id=uuid.UUID(target["document_id"]),
                        bank_id=run.bank_id,
                        revision=0,
                    )
                    session.add(state)
                state.revision = target["revision"] + 1
                state.extraction_cache = {}
                state.content_snapshot = {
                    "version": 1,
                    "content": "",
                    "chunks": [],
                    "file_summary_policy": True,
                }
                state.status = "pending"
            await self.repository._invalidate_mental_model_sources(
                session, fact_ids + obs_ids
            )
            affected_docs = {t["document_id"] for t in run.manifest["targets"]} | {
                o["document_id"] for o in run.manifest["observations"]
            }
            for identity in affected_docs:
                self.repository._enqueue_graph_event(
                    session, uuid.UUID(identity), "replace"
                )
            await session.flush()
            run.status = "retired"
            run.progress = {
                **run.progress,
                **{
                    t["document_id"]: {
                        **run.progress.get(t["document_id"], {}),
                        "stage": "retired",
                    }
                    for t in run.manifest["targets"]
                },
            }
            run.retired_fingerprint = digest(
                await self._snapshot(session, run.manifest)
            )
            return dict(run.progress)

    async def restore_retired(
        self, run_id: str, *, backup_path: Path | None = None
    ) -> None:
        """Only before new retention; intervening writes force forward recovery."""
        async with self.sessions() as session, session.begin():
            run = await self._run(session, run_id, lock=True)
            if backup_path is not None:
                exported = json.loads(backup_path.read_text(encoding="utf-8"))
                checksum = exported.pop("checksum", None)
                if (
                    checksum != digest(exported)
                    or exported.get("run_id") != run_id
                    or exported.get("manifest") != run.manifest
                    or exported.get("backup") != run.backup
                ):
                    raise ValueError("backup checksum or run identity mismatch")
                run.backup = exported["backup"]
            if run.status != "retired":
                raise ValueError("only an untouched retired run can be restored")
            await self._lock_manifest(session, run.manifest)
            if (
                digest(await self._snapshot(session, run.manifest))
                != run.retired_fingerprint
            ):
                raise ValueError("intervening writes prevent restoration")
            for target in run.manifest["targets"]:
                document = await session.get(Document, uuid.UUID(target["document_id"]))
                if (
                    document is None
                    or digest(document.raw_text) != target["source_hash"]
                ):
                    raise ValueError("document changed; use forward recovery")
            before_jobs = {
                (r["bank_id"], r["model_id"])
                for r in run.backup["mental_model_refresh_jobs"]
            }
            after = await self._snapshot(session, run.manifest)
            for row in after["mental_model_refresh_jobs"]:
                if (row["bank_id"], row["model_id"]) not in before_jobs:
                    job = await session.scalar(
                        select(MentalModelRefreshJob).where(
                            MentalModelRefreshJob.model_id == row["model_id"],
                            MentalModelRefreshJob.bank_id == row["bank_id"],
                        )
                    )
                    if job is not None:
                        await session.delete(job)
            original_states = {
                r["document_id"] for r in run.backup["hindsight_document_state"]
            }
            for row in after["hindsight_document_state"]:
                if row["document_id"] not in original_states:
                    state = await session.get(
                        HindsightDocumentState, uuid.UUID(row["document_id"])
                    )
                    if state is not None:
                        await session.delete(state)
            for table_name, rows in run.backup.items():
                model = _TABLES[table_name]
                table = model.__table__
                primary = [c.name for c in table.primary_key]
                updates = ", ".join(
                    f'"{c.name}" = EXCLUDED."{c.name}"'
                    for c in table.columns
                    if c.name not in primary
                )
                keys = ", ".join(f'"{name}"' for name in primary)
                for row in rows:
                    await session.execute(
                        text(
                            f'INSERT INTO "{table_name}" SELECT * FROM jsonb_populate_record(NULL::"{table_name}", CAST(:row AS jsonb)) '
                            f"ON CONFLICT ({keys}) DO UPDATE SET {updates}"
                        ),
                        {"row": json.dumps(row)},
                    )
            for identity in {t["document_id"] for t in run.manifest["targets"]} | {
                o["document_id"] for o in run.manifest["observations"]
            }:
                self.repository._enqueue_graph_event(
                    session, uuid.UUID(identity), "replace"
                )
            run.status = "restored"


async def _cli() -> None:
    import argparse
    from src.engine.components.store.postgres import async_session_factory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "plan", "retire", "restore"))
    parser.add_argument("--bank", required=True)
    parser.add_argument("--document-id", action="append")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rebuild = FileMemoryRebuild(
        async_session_factory, scope=MemoryScope(bank_id=args.bank)
    )
    if args.action == "preview":
        if args.output is None:
            parser.error("preview requires --output")
        manifest = await rebuild.preview(args.document_id)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    "targets": len(manifest["targets"]),
                    "observations": len(manifest["observations"]),
                    "ambiguous": len(manifest["ambiguous_documents"]),
                }
            )
        )
    elif args.action == "plan":
        if args.manifest is None or args.backup is None:
            parser.error("plan requires --manifest and --backup")
        run_id = await rebuild.create_run(
            json.loads(args.manifest.read_text(encoding="utf-8"))
        )
        await rebuild.export_backup(run_id, args.backup)
        print(json.dumps({"run_id": run_id, "status": "planned"}))
    else:
        if args.run_id is None:
            parser.error("retire/restore require --run-id")
        if args.action == "retire":
            await rebuild.retire(args.run_id)
        else:
            await rebuild.restore_retired(args.run_id, backup_path=args.backup)
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "status": (await rebuild.get_run(args.run_id)).status,
                }
            )
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(_cli())
