"""Page leases, conservative billing reservations, and recoverable publication."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid

from sqlalchemy import select

from .models import PPTJob, PPTPage
from .provider import ImageProviderError
from .store import backend_identity
from .composition import compose, regions

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
# Multi-image QA exceeded the former 4096-token estimate in live acceptance.
# These are conservative reservations, not provider-guaranteed quotations.
TOKEN_RESERVATIONS = {"image": 20000, "qa": 20000}


def now():
    return datetime.now(timezone.utc)


class PPTWorker:
    def __init__(self, store, provider, reviewer, assembler):
        self.store, self.provider = store, provider
        self.reviewer, self.assembler = reviewer, assembler

    async def claim(self):
        async with self.store.sessions() as session, session.begin():
            jobs = (
                await session.scalars(
                    select(PPTJob)
                    .where(
                        PPTJob.status.in_(
                            [
                                "awaiting_sample_approval",
                                "queued",
                                "generating",
                                "reviewing",
                                "assembling",
                            ]
                        )
                    )
                    .order_by(PPTJob.created_at)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for job in jobs:
                try:
                    await self.store.authorized(session, job)
                    if job.backend != backend_identity():
                        raise PermissionError("PPT backend changed; approval required")
                except PermissionError:
                    job.status, job.error = "failed", "authorization_or_source_changed"
                    self.store.event(session, job, "blocked", reason=job.error)
                    continue
                if job.approvals.get("outline") != job.revision:
                    continue
                pages = (
                    await session.scalars(
                        select(PPTPage)
                        .where(PPTPage.job_id == job.id)
                        .order_by(PPTPage.number)
                    )
                ).all()
                for page in pages:
                    if (
                        page.status in {"generating", "reviewing"}
                        and page.expires_at <= now()
                    ):
                        if page.status == "generating":
                            recovered = self.recover(job.id, page.lease)
                            if recovered:
                                self.settle(job, recovered.get("usage"), "image")
                                page.result, page.status = recovered, "generated"
                                self.store.event(
                                    session, job, "recovered_image", page=page.number
                                )
                            else:
                                page.status, page.error = (
                                    "unknown",
                                    "provider_outcome_unknown",
                                )
                                job.status, job.error = "failed", page.error
                                self.store.event(
                                    session, job, "unknown", page=page.number
                                )
                        else:
                            # QA is also a paid request. Do not assume a crashed
                            # reviewer consumed no tokens or retry it implicitly.
                            page.status, page.error = "unknown", "qa_outcome_unknown"
                            job.status, job.error = "failed", page.error
                        page.lease = None
                if job.status == "failed":
                    continue
                if any(p.status in {"generating", "reviewing"} for p in pages):
                    continue
                full = job.approvals.get("sample") == job.revision
                eligible = pages if full else pages[:1]
                for page in eligible:
                    if page.status not in {"pending", "generated"}:
                        continue
                    kind = "image" if page.status == "pending" else "qa"
                    if kind == "image":
                        cached = await self.cached(session, job, page)
                        if cached:
                            page.result, page.qa, page.status = (
                                cached.result,
                                cached.qa,
                                "accepted",
                            )
                            self.store.event(
                                session, job, "cache_hit", page=page.number
                            )
                            continue
                    if not self.reserve(job, kind):
                        job.status, job.error = (
                            "paused_budget",
                            "budget_exhausted_or_unpriced",
                        )
                        self.store.event(
                            session, job, "paused_budget", page=page.number
                        )
                        break
                    page.lease = str(uuid.uuid4())
                    page.expires_at = now() + timedelta(seconds=LEASE_SECONDS)
                    page.status = "generating" if kind == "image" else "reviewing"
                    if kind == "image":
                        page.attempt += 1
                    if full:
                        job.status = page.status
                    self.store.event(
                        session,
                        job,
                        "dispatch",
                        page=page.number,
                        kind_of_call=kind,
                        lease=page.lease,
                    )
                    return {
                        "job": job.id,
                        "page": page.number,
                        "revision": job.revision,
                        "lease": page.lease,
                        "kind": kind,
                        "spec": job.spec,
                        "result": page.result,
                        "sample": pages[0].result if full else None,
                        "sources": job.sources,
                        "error": page.error,
                    }
                if full and all(p.status == "accepted" for p in pages):
                    # Assembly is local/idempotent. A single advisory lock in
                    # assemble() prevents simultaneous publication by workers.
                    job.status = "assembling"
                    return {"job": job.id, "revision": job.revision, "kind": "assemble"}
        return None

    async def cached(self, session, job, page):
        candidates = (
            await session.scalars(
                select(PPTPage)
                .where(
                    PPTPage.cache_key == page.cache_key,
                    PPTPage.status == "accepted",
                    PPTPage.job_id != job.id,
                )
                .limit(20)
            )
        ).all()
        for other in candidates:
            owner = await session.get(PPTJob, other.job_id)
            try:
                await self.store.authorized(session, owner)
                path = self.store.path(other.result["path"])
                if (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    == other.result["sha256"]
                ):
                    return other
            except (PermissionError, OSError, KeyError):
                continue
        return None

    @staticmethod
    def reserve(job, kind):
        account = dict(job.accounting)
        hold = TOKEN_RESERVATIONS[kind]
        if (
            kind == "image"
            and account.get("image_attempts", 0) >= job.budget["image_attempts"]
        ):
            return False
        if kind == "qa" and account.get("qa_attempts", 0) >= 3 * len(job.spec["pages"]):
            return False
        # AFP and currency quotes are not returned by this backend. Never
        # pretend an unknown price fits a configured monetary budget.
        if job.budget.get("afp") is not None or job.budget.get("microusd") is not None:
            return False
        if job.budget.get("tokens") is not None and (
            account.get("unknown_usage", 0)
            or account.get("tokens", 0) + account.get("held_tokens", 0) + hold
            > job.budget["tokens"]
        ):
            return False
        key = kind + "_attempts"
        account[key] = account.get(key, 0) + 1
        account["held_tokens"] = account.get("held_tokens", 0) + hold
        job.accounting = account
        return True

    @staticmethod
    def settle(job, usage, kind):
        account = dict(job.accounting)
        held = TOKEN_RESERVATIONS[kind]
        account["held_tokens"] = max(0, account.get("held_tokens", 0) - held)
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        if type(total) is int and total >= 0:
            account["tokens"] = account.get("tokens", 0) + total
        else:
            account["unknown_usage"] = account.get("unknown_usage", 0) + 1
        job.accounting = account

    def recover(self, job, lease):
        if not lease:
            return None
        path = self.store.path(f"{job}/{lease}/result.json")
        try:
            record = json.loads(path.read_text())
            image = self.store.path(record["path"])
            if hashlib.sha256(image.read_bytes()).hexdigest() == record["sha256"]:
                return record
        except (OSError, ValueError, KeyError):
            pass
        return None

    async def heartbeat(self, claim):
        while True:
            await asyncio.sleep(20)
            async with self.store.sessions() as session, session.begin():
                job = await session.get(PPTJob, claim["job"], with_for_update=True)
                page = await session.get(PPTPage, (claim["job"], claim["page"]))
                if page.lease != claim["lease"] or job.revision != claim["revision"]:
                    return
                page.expires_at = now() + timedelta(seconds=LEASE_SECONDS)

    async def references(self, claim):
        from src.engine.components.store.models import Document
        from pathlib import Path

        refs = []
        async with self.store.sessions() as session:
            job = await session.get(PPTJob, claim["job"])
            await self.store.authorized(session, job)
            if job.status == "cancelled" or job.revision != claim["revision"]:
                raise PermissionError("PPT cancelled or revised")
            for identifier in claim["spec"]["pages"][claim["page"] - 1][
                "reference_document_ids"
            ]:
                row = await session.get(Document, uuid.UUID(identifier))
                refs.append(Path(row.file_path).read_bytes())
            if claim.get("sample") and claim["page"] != 1:
                refs.append(self.store.path(claim["sample"]["path"]).read_bytes())
        return tuple(refs)

    async def run_once(self):
        claim = await self.claim()
        if claim is None:
            return False
        if claim["kind"] == "assemble":
            await self.assemble(claim)
            return True
        heartbeat = asyncio.create_task(self.heartbeat(claim))
        result = None
        error = None
        try:
            references = await self.references(claim)
            if claim["kind"] == "image":
                page = claim["spec"]["pages"][claim["page"] - 1]
                prompt = json.dumps(
                    {
                        "task": "Generate one complete 16:9 slide. Render exact text. No page numbers. Vary layout by page role.",
                        "style": claim["spec"]["style"],
                        "context": claim["spec"]["context"],
                        "title": page["title"],
                        "points": page["points"],
                        "layout": page["layout"],
                        "repair": claim.get("error"),
                    },
                    ensure_ascii=False,
                )
                required = len(page["reference_document_ids"])
                if required:
                    prompt = (
                        "生成16:9演示文稿底图，2560×1440。中间绝大部分留空，"
                        "这是供程序随后嵌入真实原图的底图，不是完整成品。"
                        "中央从y=216到1224的全部区域只填纯背景色，禁止图形、文字、"
                        "卡片、装饰、边框或占位提示。最上方12%以内写标题，基线y=150。"
                        "最底边只写一行小号文字，字号约48px、基线y=1360，"
                        "所有要点用竖线连接成同一行居中。禁止列表、换行和分栏卡片。"
                        "不要重复。所有字形不得越出这两个条带。"
                        "不要生成页码。\n"
                        + json.dumps(
                            {
                                "style": claim["spec"]["style"],
                                "top_strip_title": page["title"],
                                "bottom_single_line": "  |  ".join(page["points"]),
                                "empty_reference_regions": regions(
                                    page["reference_document_ids"]
                                ),
                            },
                            ensure_ascii=False,
                        )
                    )
                elif references:
                    prompt = (
                        "唯一参考图片仅供整页配色和字体风格参考，"
                        "不要复制它的布局或内容，不要将它当成必需素材。\n" + prompt
                    )
                generated = await self.provider.generate(
                    prompt, references=() if required else references
                )
                # Local composition failures must not discard known billing.
                result = {"usage": generated.usage, "request_id": generated.request_id}
                folder = self.store.path(f"{claim['job']}/{claim['lease']}")
                folder.mkdir(parents=True, exist_ok=True)
                suffix = "png" if generated.mime == "image/png" else "jpg"
                background = folder / f"background.{suffix}"
                background.write_bytes(generated.data)
                data, embedded = await asyncio.to_thread(
                    compose,
                    generated.data,
                    page["reference_document_ids"],
                    references[:required],
                )
                path = folder / "slide.png"
                path.write_bytes(data)
                result = {
                    "path": str(path.relative_to(self.store.root)).replace("\\", "/"),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mime": "image/png",
                    "background": {
                        "path": str(background.relative_to(self.store.root)).replace(
                            "\\", "/"
                        ),
                        "sha256": generated.sha256,
                    },
                    "embedded": embedded,
                    "requested_model": generated.requested_model,
                    "actual_model": generated.actual_model,
                    "request_id": generated.request_id,
                    "usage": generated.usage,
                }
                temporary = folder / "result.tmp"
                temporary.write_text(json.dumps(result), encoding="utf-8")
                temporary.replace(folder / "result.json")
            else:
                result = await self.reviewer(claim, references)
        except Exception as exc:
            error = exc
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await self.finish(claim, result, error)
        return True

    async def finish(self, claim, result, error=None):
        async with self.store.sessions() as session, session.begin():
            job = await session.get(PPTJob, claim["job"], with_for_update=True)
            page = await session.get(PPTPage, (claim["job"], claim["page"]))
            if (
                page.lease != claim["lease"]
                or job.revision != claim["revision"]
                or page.expires_at <= now()
            ):
                return False
            self.settle(job, result.get("usage") if result else None, claim["kind"])
            page.lease, page.expires_at = None, None
            try:
                await self.store.authorized(session, job)
            except PermissionError:
                job.status, job.error = "failed", "authorization_or_source_changed"
            if job.status in {"cancelled", "failed"}:
                page.status = "cancelled"
                self.store.event(session, job, "late_result", page=page.number)
                return False
            if error is not None:
                unknown = isinstance(error, ImageProviderError) and error.unknown
                page.status = "unknown" if unknown else "failed"
                page.error = (
                    error.code
                    if isinstance(error, ImageProviderError)
                    else type(error).__name__
                )
                job.status, job.error = "failed", page.error
            elif claim["kind"] == "image":
                page.result, page.status, page.error = result, "generated", None
            else:
                page.qa = result
                if result.get("passed") is True:
                    page.status, page.error = "accepted", None
                elif page.repairs < 1:
                    page.repairs += 1
                    page.status, page.error = (
                        "pending",
                        str(result.get("reason", "visual_check_failed"))[:2000],
                    )
                else:
                    page.status, page.error = "failed", "visual_check_failed"
                    job.status, job.error = "failed", page.error
            if (
                job.budget.get("tokens") is not None
                and job.accounting.get("tokens", 0) > job.budget["tokens"]
            ):
                job.status, job.error = (
                    "paused_budget",
                    "reported_usage_exceeded_reservation",
                )
            self.store.event(
                session,
                job,
                "result",
                page=page.number,
                status=page.status,
                usage=result.get("usage") if result else None,
            )
            return True

    async def assemble(self, claim):
        from sqlalchemy import text

        # Transaction-level advisory lock on a stable job hash. Assembly only
        # reads <=20 local images; never holds this lock across a model call.
        async with self.store.sessions() as session, session.begin():
            lock = int(hashlib.sha256(claim["job"].encode()).hexdigest()[:15], 16)
            if not await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock}
            ):
                return
            job = await session.get(PPTJob, claim["job"], with_for_update=True)
            if job.status != "assembling" or job.revision != claim["revision"]:
                return
            try:
                await self.store.authorized(session, job)
                pages = (
                    await session.scalars(
                        select(PPTPage)
                        .where(PPTPage.job_id == job.id)
                        .order_by(PPTPage.number)
                    )
                ).all()
                if not all(p.status == "accepted" for p in pages):
                    raise ValueError("Unverified pages")
                job.artifact = await asyncio.to_thread(self.assembler, job, pages)
                job.status = "completed"
                self.store.event(session, job, "completed")
            except Exception as exc:
                job.status, job.error = "failed", type(exc).__name__
                self.store.event(session, job, "assembly_failed", reason=job.error)

    async def run(self):
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("PPT worker iteration failed")
                await asyncio.sleep(2)
