"""Budgeted, resumable summary retention and scoped consolidation for a manifest."""

from __future__ import annotations

from dataclasses import replace
import json
import uuid

from sqlalchemy import func, select, text

from src.engine.components.analyzer import Analyzer
from src.engine.components.file_summary import FileSummaryManager
from src.engine.components.store.models import Document
from src.engine.scope import MemoryScope
from .config import HindsightOptions
from .consolidation import (
    ConsolidationWorker,
    ConsolidationOptions,
    PostgresConsolidationRepository,
)
from .consolidation_actions import EvidenceVersion
from .file_rebuild import FileMemoryRebuild, digest
from .models import (
    ConsolidationFactEvent,
    ConsolidationJob,
    HindsightGraphOutbox,
    MemoryUnit,
    ObservationEvidence,
)
from .retain import RetainEngine
from .types import RetainInput


class BudgetPaused(BaseException):
    """Control signal: extraction's failure-isolation must not publish it as empty."""


class BudgetedProviders:
    def __init__(
        self,
        rebuild,
        run_id,
        providers,
        *,
        max_tokens=100000,
        max_cost_usd=0,
        input_price=0,
        output_price=0,
    ):
        if max_tokens < 1 or max_cost_usd < 0 or min(input_price, output_price) < 0:
            raise ValueError("invalid rebuild budget")
        if max_cost_usd and not (input_price or output_price):
            raise ValueError("cost budget requires token prices")
        self.rebuild, self.run_id, self.providers = rebuild, run_id, providers
        self.max_tokens, self.max_cost_usd = max_tokens, max_cost_usd
        self.input_price, self.output_price = input_price, output_price

    async def json_with_usage(self, system, user, *, max_tokens=None, **kwargs):
        output_limit = min(max_tokens or 2048, 2048)
        # Conservative byte bound plus protocol overhead, before any network call.
        reserve = len(system.encode()) + len(user.encode()) + output_limit + 4096
        reserve_cost = reserve * max(self.input_price, self.output_price) / 1_000_000
        async with self.rebuild.sessions() as session, session.begin():
            run = await self.rebuild._run(session, self.run_id, lock=True)
            ledger = dict(run.progress.get("_budget", {}))
            if ledger.get("charged_tokens", 0) + reserve > self.max_tokens or (
                self.max_cost_usd
                and ledger.get("charged_cost_usd", 0) + reserve_cost > self.max_cost_usd
            ):
                raise BudgetPaused("run budget cannot reserve another model call")
            ledger.update(
                charged_tokens=ledger.get("charged_tokens", 0) + reserve,
                charged_cost_usd=ledger.get("charged_cost_usd", 0) + reserve_cost,
                calls=ledger.get("calls", 0) + 1,
            )
            run.progress = {**run.progress, "_budget": ledger}
        # Failed or interrupted calls keep their reservation; resume never loses
        # spent quota merely because a process died before writing telemetry.
        loader = getattr(self.providers, "json_with_usage", None)
        if loader is None:
            payload = await self.providers.json(
                system, user, max_tokens=output_limit, **kwargs
            )
            usage = {}
        else:
            payload, usage = await loader(
                system, user, max_tokens=output_limit, **kwargs
            )
        actual = usage.get("total_tokens")
        if actual is None and "prompt_tokens" in usage and "completion_tokens" in usage:
            actual = usage["prompt_tokens"] + usage["completion_tokens"]
        cost_known = "prompt_tokens" in usage and "completion_tokens" in usage
        cost = (
            usage.get("prompt_tokens", 0) * self.input_price
            + usage.get("completion_tokens", 0) * self.output_price
        ) / 1_000_000
        async with self.rebuild.sessions() as session, session.begin():
            run = await self.rebuild._run(session, self.run_id, lock=True)
            ledger = dict(run.progress["_budget"])
            if actual is not None:
                ledger["charged_tokens"] += int(actual) - reserve
                ledger["actual_tokens"] = ledger.get("actual_tokens", 0) + int(actual)
            else:
                ledger["estimated_calls"] = ledger.get("estimated_calls", 0) + 1
            if cost_known:
                ledger["charged_cost_usd"] += cost - reserve_cost
            run.progress = {**run.progress, "_budget": ledger}
        return payload, usage

    async def json(self, system, user, **kwargs):
        return (await self.json_with_usage(system, user, **kwargs))[0]

    async def embed(self, texts, **kwargs):
        return await self.providers.embed(texts, **kwargs)


class BudgetedAnalyzer(Analyzer):
    def __init__(self, providers):
        super().__init__()
        self.providers = providers

    async def _call_openai_compatible(self, prompt, *, max_tokens=None):
        payload = await self.providers.json(
            "Summarize only supplied file content.", prompt, max_tokens=max_tokens
        )
        return json.dumps(payload, ensure_ascii=False)


class RebuildConsolidationRepository(PostgresConsolidationRepository):
    def __init__(self, sessions, manifest, scope_keys):
        super().__init__(sessions, bank_id=manifest["bank_id"], scope_keys=scope_keys)
        self.manifest = manifest
        self.last_claim = None

    async def claim(self, options):
        self.last_claim = await super().claim(options)
        return self.last_claim

    async def read_set(self, claim, options):
        async with self._session_factory() as session:
            other_sources = await session.scalar(
                select(func.count())
                .select_from(ConsolidationFactEvent)
                .where(
                    ConsolidationFactEvent.bank_id == claim.bank_id,
                    ConsolidationFactEvent.scope_key == claim.scope_key,
                    ConsolidationFactEvent.id > claim.processed_through,
                    ConsolidationFactEvent.id <= claim.claimed_through,
                    ConsolidationFactEvent.document_id.not_in(
                        [uuid.UUID(t["document_id"]) for t in self.manifest["targets"]]
                    ),
                )
            )
            if other_sources:
                raise ValueError(
                    "unrelated pending consolidation must drain before migration"
                )
        original = await super().read_set(claim, options)
        doc_ids = {t["document_id"] for t in self.manifest["targets"]}
        survivor_ids = {
            i for o in self.manifest["observations"] for i in o["retained_fact_ids"]
        }
        async with self._session_factory() as session:
            survivors = list(
                await session.scalars(
                    select(MemoryUnit).where(
                        MemoryUnit.id.in_([uuid.UUID(i) for i in survivor_ids]),
                        MemoryUnit.bank_id == claim.bank_id,
                        MemoryUnit.state == "active",
                        MemoryUnit.scope_tags.contains(list(claim.write_scope)),
                        MemoryUnit.memory_type.in_(("world", "experience")),
                        MemoryUnit.is_source_chunk.is_(False),
                    )
                )
            )
        rows = {
            key: value
            for key, value in original.fact_rows.items()
            if str(value.document_id) in doc_ids or key in survivor_ids
        }
        rows.update({str(row.id): row for row in survivors})
        observations = {
            key: value
            for key, value in original.observations.items()
            if key not in self.manifest.get("protected_observation_ids", ())
            and set(value.source_fact_ids).intersection(rows)
        }
        return replace(
            original,
            facts={
                key: EvidenceVersion(
                    key,
                    row.memory_version,
                    row.bank_id,
                    tuple(row.scope_tags),
                    row.state,
                )
                for key, row in rows.items()
            },
            fact_rows=rows,
            observations=observations,
            observation_rows={
                key: original.observation_rows[key] for key in observations
            },
        )


class FileRebuildRunner:
    def __init__(
        self,
        rebuild: FileMemoryRebuild,
        providers,
        graph_worker,
        *,
        budget_tokens=100000,
        budget_cost_usd=0,
        input_price=0,
        output_price=0,
        fault=None,
    ):
        self.rebuild, self.providers, self.graph_worker = (
            rebuild,
            providers,
            graph_worker,
        )
        self.budget_options = dict(
            max_tokens=budget_tokens,
            max_cost_usd=budget_cost_usd,
            input_price=input_price,
            output_price=output_price,
        )
        self.fault = fault

    @property
    def scope_keys(self):
        return [
            json.dumps(sorted(s), separators=(",", ":"), ensure_ascii=False)
            for s in self.rebuild.scope.observation_scopes or ((),)
        ]

    async def _check_retired(self, target, value):
        from .request_identity import request_fingerprint

        if (
            await self.rebuild.repository.retention_request_result(
                value.document_id, value.request_id, request_fingerprint(value)
            )
            is not None
        ):
            return
        async with self.rebuild.sessions() as session:
            for identity, version in target["fact_versions"].items():
                row = await session.get(MemoryUnit, uuid.UUID(identity))
                if (
                    row is None
                    or row.state != "retired"
                    or row.memory_version != version + 1
                ):
                    raise ValueError("intervening fact write; replan migration")

    async def _checkpoint(self, run_id, document_id, stage, **details):
        async with self.rebuild.sessions() as session, session.begin():
            run = await self.rebuild._run(session, run_id, lock=True)
            previous = run.progress.get(document_id, {})
            run.progress = {
                **run.progress,
                document_id: {
                    **previous,
                    "stage": stage,
                    "history": [*previous.get("history", []), stage],
                    **details,
                },
            }
        if self.fault is not None:
            await self.fault(stage, document_id)

    async def _source(self, target):
        async with self.rebuild.sessions() as session:
            document = await session.scalar(
                select(Document).where(
                    Document.id == uuid.UUID(target["document_id"]),
                    self.rebuild.repository._document_scope(),
                )
            )
            if (
                document is None
                or digest(document.raw_text) != target["source_hash"]
                or document.title != target["title"]
                or list(document.tags or ()) != target["tags"]
            ):
                raise ValueError("document changed; replan migration")
            return document

    async def resume(self, run_id: str, *, batch_size: int = 1, max_steps: int = 32):
        if batch_size < 1 or max_steps < 1:
            raise ValueError("batch size and steps must be positive")
        # Session advisory lock survives commits without holding a DB transaction
        # over LLM I/O. A second process cannot execute the same run concurrently.
        engine = self.rebuild.sessions.kw["bind"]
        async with engine.connect() as connection:
            key = f"tkb-file-rebuild:{run_id}"
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": key},
            )
            await connection.commit()
            if not acquired:
                return {"status": "busy", "run_id": run_id}
            try:
                return await self._resume(run_id, batch_size, max_steps)
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": key},
                )
                await connection.commit()

    async def _resume(self, run_id, batch_size, max_steps):
        run = await self.rebuild.get_run(run_id)
        if run.status in {"verified", "restored"}:
            return {"status": run.status, "run_id": run_id}
        budget = BudgetedProviders(
            self.rebuild, run_id, self.providers, **self.budget_options
        )
        consolidation_repo = RebuildConsolidationRepository(
            self.rebuild.sessions, run.manifest, self.scope_keys
        )
        try:
            prepared = 0
            if run.status in {"planned", "retired"}:
                async with self.rebuild.sessions() as session:
                    pending = await session.scalar(
                        select(func.count())
                        .select_from(ConsolidationFactEvent)
                        .join(
                            ConsolidationJob,
                            (ConsolidationJob.bank_id == ConsolidationFactEvent.bank_id)
                            & (
                                ConsolidationJob.scope_key
                                == ConsolidationFactEvent.scope_key
                            ),
                        )
                        .where(
                            ConsolidationFactEvent.bank_id == run.bank_id,
                            ConsolidationFactEvent.scope_key.in_(self.scope_keys),
                            ConsolidationFactEvent.id
                            > ConsolidationJob.processed_through,
                            ConsolidationFactEvent.document_id.not_in(
                                [
                                    uuid.UUID(t["document_id"])
                                    for t in run.manifest["targets"]
                                ]
                            ),
                        )
                    )
                if pending:
                    return {
                        "status": "awaiting_unrelated_consolidation",
                        "run_id": run_id,
                    }
                manager = FileSummaryManager(
                    self.rebuild.sessions,
                    analyzer=BudgetedAnalyzer(budget),
                    scope=self.rebuild.scope,
                )
                for target in run.manifest["targets"]:
                    identity = target["document_id"]
                    saved_key = run.progress.get(identity, {}).get("summary_key")
                    if saved_key:
                        from config.settings import settings
                        from src.engine.components.store.file_summary import (
                            SummaryIdentity,
                        )

                        document = await self._source(target)
                        model = (
                            settings.llm.require_model()
                            if settings.llm.enabled
                            else "extractive"
                        )
                        if (
                            SummaryIdentity.for_text(
                                document.raw_text, document.title, model
                            ).key
                            != saved_key
                        ):
                            raise ValueError("summary policy changed; replan migration")
                        continue
                    if prepared >= batch_size:
                        return {"status": "preparing", "run_id": run_id}
                    document = await self._source(target)
                    summary = await manager.prepare(
                        identity, document.raw_text, document.title
                    )
                    await self._checkpoint(
                        run_id,
                        identity,
                        "summary_ready" if run.status == "planned" else "retired",
                        summary_key=summary.identity.key,
                    )
                    prepared += 1
                if run.status == "planned":
                    await self.rebuild.retire(run_id)
                    await self.graph_worker.drain(limit=max_steps)
                    if self.fault is not None:
                        await self.fault("retired", None)
            run = await self.rebuild.get_run(run_id)
            retained = 0
            for target in run.manifest["targets"]:
                identity = target["document_id"]
                if run.progress.get(identity, {}).get("stage") in {
                    "retained",
                    "consolidated",
                    "verified",
                    "empty",
                }:
                    continue
                if retained >= batch_size:
                    return {"status": "retaining", "run_id": run_id}
                document = await self._source(target)
                value = RetainInput(
                    document_id=identity,
                    title=document.title,
                    content=document.raw_text,
                    file_type=document.file_type,
                    source_type="historical-backfill",
                    tags=tuple(document.tags or ()),
                    expected_revision=target["revision"] + 1,
                    request_id=f"file-rebuild:{run_id}:{identity}",
                )
                await self._check_retired(target, value)
                result = await RetainEngine(
                    self.rebuild.repository,
                    budget,
                    HindsightOptions(
                        file_summary_enabled=True,
                        consolidation_enabled=True,
                        retain_chunk_concurrency=1,
                    ),
                ).retain(value)
                if self.fault is not None:
                    await self.fault("retain_committed", identity)
                if result.status not in {"success", "empty"}:
                    raise ValueError("summary fact extraction incomplete")
                await self._checkpoint(
                    run_id,
                    identity,
                    "retained",
                    facts=result.facts,
                    retained_revision=result.revision,
                )
                retained += 1
            worker = ConsolidationWorker(
                consolidation_repo,
                budget,
                ConsolidationOptions(
                    max_output_tokens=2048, semantic_dedup_enabled=False
                ),
            )
            for _ in range(max_steps):
                result = await worker.run_once()
                if result is None:
                    break
            # The graph worker is constructed with bank/document filters by CLI.
            await self.graph_worker.drain(limit=max_steps)
            return await self.verify(run_id)
        except BudgetPaused as error:
            if consolidation_repo.last_claim is not None:
                await consolidation_repo.fail(
                    consolidation_repo.last_claim,
                    RuntimeError("migration_budget_paused"),
                )
            return {"status": "budget_paused", "run_id": run_id, "reason": str(error)}

        except Exception as error:
            async with self.rebuild.sessions() as session, session.begin():
                failed = await self.rebuild._run(session, run_id, lock=True)
                failed.progress = {
                    **failed.progress,
                    "_last_error": type(error).__name__,
                }
            return {"status": "failed", "run_id": run_id, "error": type(error).__name__}

    async def verify(self, run_id):
        async with self.rebuild.sessions() as session, session.begin():
            run = await self.rebuild._run(session, run_id, lock=True)
            old_ids = [
                uuid.UUID(i)
                for t in run.manifest["targets"]
                for i in t["fact_versions"]
            ]
            doc_ids = {uuid.UUID(t["document_id"]) for t in run.manifest["targets"]} | {
                uuid.UUID(o["document_id"]) for o in run.manifest["observations"]
            }
            doc_ids.update(
                uuid.UUID(i) for i in run.manifest.get("graph_document_ids", [])
            )
            old_obs = [uuid.UUID(o["id"]) for o in run.manifest["observations"]]
            old_active = await session.scalar(
                select(func.count())
                .select_from(MemoryUnit)
                .where(
                    MemoryUnit.id.in_(old_ids + old_obs),
                    MemoryUnit.state.in_(("active", "stale")),
                )
            )
            edges = await session.scalar(
                select(func.count())
                .select_from(ObservationEvidence)
                .where(
                    ObservationEvidence.fact_id.in_(old_ids),
                    ObservationEvidence.active.is_(True),
                )
            )
            graph_pending = await session.scalar(
                select(func.count())
                .select_from(HindsightGraphOutbox)
                .where(
                    HindsightGraphOutbox.bank_id == run.bank_id,
                    HindsightGraphOutbox.document_id.in_(doc_ids),
                    HindsightGraphOutbox.status != "completed",
                )
            )
            jobs_pending = await session.scalar(
                select(func.count())
                .select_from(ConsolidationJob)
                .where(
                    ConsolidationJob.bank_id == run.bank_id,
                    ConsolidationJob.scope_key.in_(self.scope_keys),
                    ConsolidationJob.status != "completed",
                )
            )
            if old_active or edges:
                raise ValueError("legacy evidence is still active")
            if not jobs_pending:
                run.progress = {
                    **run.progress,
                    **{
                        t["document_id"]: {
                            **run.progress[t["document_id"]],
                            "stage": "consolidated",
                        }
                        for t in run.manifest["targets"]
                        if run.progress.get(t["document_id"], {}).get("stage")
                        == "retained"
                    },
                }
            if graph_pending or jobs_pending:
                return {
                    "status": "awaiting_projection_or_consolidation",
                    "run_id": run_id,
                    "graph_pending": graph_pending,
                    "jobs_pending": jobs_pending,
                }
            for target in run.manifest["targets"]:
                document = await self._source(target)
                state = run.progress.get(target["document_id"], {})
                if state.get("stage") not in {
                    "retained",
                    "consolidated",
                    "verified",
                    "empty",
                }:
                    return {"status": "incomplete", "run_id": run_id}
                facts = list(
                    await session.scalars(
                        select(MemoryUnit).where(
                            MemoryUnit.document_id == document.id,
                            MemoryUnit.memory_type.in_(("world", "experience")),
                            MemoryUnit.state == "active",
                            MemoryUnit.is_source_chunk.is_(False),
                        )
                    )
                )
                if any(
                    (f.metadata_json or {}).get("file_summary", {}).get("key")
                    != state.get("summary_key")
                    for f in facts
                ):
                    raise ValueError("new file facts lack summary provenance")
            protected = list(
                await session.scalars(
                    select(func.to_jsonb(MemoryUnit.__table__.table_valued())).where(
                        MemoryUnit.id.in_(
                            [
                                uuid.UUID(i)
                                for i in run.manifest.get("protected_memory_ids", [])
                            ]
                        )
                    )
                )
            )
            if digest(sorted(protected, key=lambda row: row["id"])) != run.manifest.get(
                "protected_memory_fingerprint"
            ):
                raise ValueError("non-target memory changed during migration")
            run.status = "verified"
            run.progress = {
                **run.progress,
                **{
                    t["document_id"]: {
                        **run.progress[t["document_id"]],
                        "stage": "verified"
                        if run.progress[t["document_id"]].get("facts")
                        else "empty",
                    }
                    for t in run.manifest["targets"]
                },
            }
            return {
                "status": "verified",
                "run_id": run_id,
                "budget": run.progress.get("_budget", {}),
            }


async def _cli() -> None:
    import argparse
    from src.engine.components.store.postgres import async_session_factory
    from .providers import ProjectHindsightProviders
    from .graph_outbox import GraphProjectionWorker, PostgresGraphOutbox
    from .graph_projector import MemoryGraphProjector
    from .neo4j_graph import HindsightNeo4jGraphStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("resume", "verify"))
    parser.add_argument("--bank", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--max-tokens", type=int, default=100000)
    parser.add_argument("--max-cost-usd", type=float, default=0)
    parser.add_argument("--input-price", type=float, default=0)
    parser.add_argument("--output-price", type=float, default=0)
    args = parser.parse_args()
    scope = MemoryScope(bank_id=args.bank)
    rebuild = FileMemoryRebuild(async_session_factory, scope=scope)
    run = await rebuild.get_run(args.run_id)
    graph = HindsightNeo4jGraphStore(scope=scope)
    worker = GraphProjectionWorker(
        PostgresGraphOutbox(
            async_session_factory,
            bank_id=args.bank,
            document_ids=[
                uuid.UUID(i) for i in run.manifest.get("graph_document_ids", [])
            ],
        ),
        rebuild.repository,
        MemoryGraphProjector(graph),
    )
    try:
        runner = FileRebuildRunner(
            rebuild,
            ProjectHindsightProviders(),
            worker,
            budget_tokens=args.max_tokens,
            budget_cost_usd=args.max_cost_usd,
            input_price=args.input_price,
            output_price=args.output_price,
        )
        result = (
            await runner.resume(
                args.run_id, batch_size=args.batch_size, max_steps=args.max_steps
            )
            if args.action == "resume"
            else await runner.verify(args.run_id)
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await graph.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_cli())
