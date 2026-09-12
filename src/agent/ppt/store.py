"""Transactional approvals, current authorization, source identities and budgets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from sqlalchemy import select

from config.settings import settings
from src.engine.components.store.models import Document, MemoryBank
from src.engine.trusted_scope import ScopeBinding
from .contracts import Budget, DeckSpec
from .models import PPTEvent, PPTJob, PPTPage
from .provider import validate_image
from .composition import POLICY, regions

UPSTREAM = "f47bd3e54e49d14d51807692694e2d5619a8e298"


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def backend_identity():
    return {
        "provider": settings.image.provider,
        "model": settings.image.model,
        "base_url": settings.image.base_url.rstrip("/"),
        "size": "2560x1440",
        "upstream": UPSTREAM,
        "composition": POLICY,
    }


class PPTStore:
    def __init__(self, sessions, root: Path):
        self.sessions = sessions
        self.root = root.resolve()

    def path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise PermissionError("Invalid artifact path")
        return path

    async def sources(self, session, spec: dict, binding: ScopeBinding):
        refs = {r for p in spec["pages"] for r in p["reference_document_ids"]}
        ids = set(spec["source_document_ids"]) | refs
        if len(ids) > 20:
            raise ValueError("Too many source documents")
        result = []
        for identifier in sorted(ids):
            row = await session.get(Document, uuid.UUID(identifier))
            if row is None or not binding.scope().permits(row.bank_id, row.tags):
                raise PermissionError("Source unavailable")
            record = {
                "id": identifier,
                "hash": digest(
                    [row.raw_text, row.title, row.version_number, row.is_current]
                ),
                "reference": identifier in refs,
            }
            if identifier in refs:
                path = Path(row.file_path or "").resolve()
                if (
                    not path.is_relative_to(Path(settings.uploads_dir).resolve())
                    or not path.is_file()
                ):
                    raise PermissionError("Reference image unavailable")
                if path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("Reference image too large")
                data = path.read_bytes()
                validate_image(data)
                record.update(image_hash=hashlib.sha256(data).hexdigest())
            result.append(record)
        for page in spec["pages"]:
            regions(page["reference_document_ids"])
        return result

    async def authorized(self, session, job, *, binding=None, authority=None):
        original = ScopeBinding.model_validate(job.binding)
        current = (
            ScopeBinding()
            if job.authority == "default"
            else settings.memory_scope_bindings.get(job.authority)
        )
        if current != original:
            raise PermissionError("PPT authorization revoked")
        bank = await session.get(MemoryBank, job.bank_id)
        if bank is None or bank.policy_version != original.policy_version:
            raise PermissionError("PPT bank policy changed")
        if binding is not None and (binding != original or authority != job.authority):
            raise PermissionError("PPT task unavailable")
        if await self.sources(session, job.spec, original) != job.sources:
            raise PermissionError("PPT source changed or unavailable")
        return original

    @staticmethod
    def event(session, job, kind, **detail):
        session.add(
            PPTEvent(job_id=job.id, revision=job.revision, kind=kind, detail=detail)
        )

    def page_key(self, job, number):
        page = job.spec["pages"][number - 1]
        return digest(
            [
                job.bank_id,
                job.authority,
                job.binding,
                job.sources,
                job.backend,
                job.spec["style"],
                job.spec["context"],
                page,
            ]
        )

    async def create(
        self,
        spec: DeckSpec,
        binding: ScopeBinding,
        authority: str,
        session_id: str,
        budget: Budget | None = None,
    ):
        if not settings.ppt.enabled:
            raise ValueError("Image presentations are disabled")
        settings.image.require_ready()
        if len(spec.pages) > settings.ppt.max_pages:
            raise ValueError("PPT page limit exceeded")
        limit = budget or Budget(image_attempts=2 * len(spec.pages))
        if limit.image_attempts > 2 * len(spec.pages):
            raise ValueError("At most two image attempts per slide")
        async with self.sessions() as session, session.begin():
            job = PPTJob(
                id=str(uuid.uuid4()),
                bank_id=binding.bank_id,
                authority=authority,
                binding=binding.model_dump(mode="json"),
                session_id=session_id,
                spec=spec.model_dump(),
                sources=await self.sources(session, spec.model_dump(), binding),
                backend=backend_identity(),
                budget=limit.model_dump(),
                revision=1,
                status="awaiting_outline_approval",
                approvals={},
                accounting={},
            )
            await self.authorized(session, job, binding=binding, authority=authority)
            session.add(job)
            await session.flush()
            for n in range(1, len(spec.pages) + 1):
                session.add(
                    PPTPage(
                        job_id=job.id,
                        number=n,
                        revision=1,
                        cache_key=self.page_key(job, n),
                    )
                )
            self.event(session, job, "created")
            return job.id

    async def get(self, identifier, binding, authority):
        async with self.sessions() as session:
            job = await session.get(PPTJob, identifier)
            if job is None:
                raise PermissionError("PPT task unavailable")
            await self.authorized(session, job, binding=binding, authority=authority)
            pages = (
                await session.scalars(
                    select(PPTPage)
                    .where(PPTPage.job_id == identifier)
                    .order_by(PPTPage.number)
                )
            ).all()
            return {
                "id": job.id,
                "revision": job.revision,
                "status": job.status,
                "spec": job.spec,
                "backend": job.backend,
                "approvals": job.approvals,
                "budget": job.budget,
                "accounting": job.accounting,
                "billing": {"afp": None, "microusd": None},
                "error": job.error,
                "artifact": job.artifact,
                "pages": [
                    {
                        "number": p.number,
                        "status": p.status,
                        "error": p.error,
                        "attempts": p.attempt,
                        "qa": p.qa,
                        "reference_regions": regions(
                            job.spec["pages"][p.number - 1]["reference_document_ids"]
                        )
                        if job.backend.get("composition") == POLICY
                        else [],
                        "preview_url": f"/api/ppt/jobs/{job.id}/pages/{p.number}"
                        if p.result
                        else None,
                    }
                    for p in pages
                ],
            }

    async def control(
        self,
        identifier,
        binding,
        authority,
        revision,
        action,
        *,
        page=None,
        spec=None,
        budget=None,
    ):
        async with self.sessions() as session, session.begin():
            job = await session.get(PPTJob, identifier, with_for_update=True)
            if job is None:
                raise PermissionError("PPT task unavailable")
            await self.authorized(session, job, binding=binding, authority=authority)
            if job.revision != revision:
                raise ValueError("Stale PPT revision; reload before approving")
            pages = (
                await session.scalars(
                    select(PPTPage)
                    .where(PPTPage.job_id == identifier)
                    .order_by(PPTPage.number)
                )
            ).all()
            if action == "cancel":
                job.status = "cancelled"
            elif (
                action == "approve_outline"
                and job.status == "awaiting_outline_approval"
            ):
                if job.backend != backend_identity():
                    raise ValueError("Backend changed; revise the deck first")
                job.approvals = {"outline": revision}
                job.status = "awaiting_sample_approval"
            elif (
                action == "approve_sample"
                and job.status == "awaiting_sample_approval"
                and pages[0].status == "accepted"
            ):
                job.approvals = {**job.approvals, "sample": revision}
                job.status = "queued"
            elif action == "retry" and job.status in {
                "failed",
                "paused_budget",
                "completed",
            }:
                if not isinstance(page, int) or not 1 <= page <= len(pages):
                    raise ValueError("Choose a failed slide")
                target = pages[page - 1]
                if target.status not in {"failed", "unknown"}:
                    raise ValueError("Only failed/unknown pages can be retried")
                if budget is not None:
                    self.set_budget(job, budget)
                # An unknown review does not invalidate the already persisted
                # image. Explicit retry pays for QA, not another image.
                target.status = (
                    "generated"
                    if target.error == "qa_outcome_unknown" and target.result
                    else "pending"
                )
                target.error = (
                    target.qa.get("reason")
                    if target.status == "pending" and target.qa
                    else None
                )
                target.lease = None
                job.status = (
                    "queued"
                    if job.approvals.get("sample") == revision
                    else "awaiting_sample_approval"
                )
                job.error = None
            elif (
                action == "budget"
                and job.status == "paused_budget"
                and budget is not None
            ):
                self.set_budget(job, budget)
                job.status = (
                    "queued"
                    if job.approvals.get("sample") == revision
                    else "awaiting_sample_approval"
                )
                job.error = None
            elif action == "revise" and spec is not None:
                if (
                    any(p.status in {"generating", "reviewing"} for p in pages)
                    or job.status == "assembling"
                ):
                    raise ValueError("Wait for in-flight work before revision")
                if len(spec.pages) != len(pages):
                    raise ValueError("Create a new job to change the page count")
                new_spec = spec.model_dump()
                style_changed = (
                    new_spec["style"] != job.spec["style"]
                    or job.backend != backend_identity()
                )
                job.spec = new_spec
                job.sources = await self.sources(session, new_spec, binding)
                job.backend = backend_identity()
                job.revision += 1
                for p in pages:
                    key = self.page_key(job, p.number)
                    if key != p.cache_key:
                        p.status, p.result, p.qa, p.error, p.repairs = (
                            "pending",
                            None,
                            None,
                            None,
                            0,
                        )
                    p.cache_key, p.revision, p.lease = key, job.revision, None
                # Approval is always revision-bound. Reusing an unchanged sample
                # is allowed, but its approval must be explicit again.
                job.approvals, job.artifact, job.error = {}, None, None
                job.status = "awaiting_outline_approval"
                if style_changed:
                    for p in pages:
                        p.status, p.result, p.qa = "pending", None, None
            else:
                raise ValueError("Action is not valid in the current PPT state")
            self.event(session, job, action, page=page)

    @staticmethod
    def set_budget(job, budget: Budget):
        if budget.image_attempts > 2 * len(job.spec["pages"]):
            raise ValueError("Image attempt hard limit exceeded")
        job.budget = budget.model_dump()

    async def file(self, identifier, binding, authority, *, page=None, artifact=False):
        async with self.sessions() as session:
            job = await session.get(PPTJob, identifier)
            if job is None:
                raise PermissionError("PPT task unavailable")
            await self.authorized(session, job, binding=binding, authority=authority)
            if artifact:
                if job.status != "completed" or not job.artifact:
                    raise ValueError("PPT is not complete")
                record = job.artifact
            else:
                row = await session.get(PPTPage, (identifier, page))
                if row is None or not row.result:
                    raise ValueError("Preview unavailable")
                record = row.result
            path = self.path(record["path"])
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
            ):
                raise ValueError("Artifact integrity check failed")
            return path

    async def reference_file(self, identifier, document_id, binding, authority):
        async with self.sessions() as session:
            job = await session.get(PPTJob, identifier)
            if job is None:
                raise PermissionError("PPT task unavailable")
            await self.authorized(session, job, binding=binding, authority=authority)
            if not any(s["id"] == document_id and s["reference"] for s in job.sources):
                raise PermissionError("Reference unavailable")
            doc = await session.get(Document, uuid.UUID(document_id))
            return Path(doc.file_path).resolve()
