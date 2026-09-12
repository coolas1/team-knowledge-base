"""Disposable schema tests; no external model calls."""

from datetime import timedelta
import hashlib
import json
import os
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config.settings import settings
from src.agent.ppt.contracts import Budget, DeckSpec, PageSpec
from src.agent.ppt.models import PPTJob, PPTPage, migrate
from src.agent.ppt.store import PPTStore
from src.agent.ppt.worker import PPTWorker, now
from src.engine.components.store.models import Document, MemoryBank
from src.engine.trusted_scope import ScopeBinding

pytestmark = pytest.mark.integration


@pytest.fixture
async def context(tmp_path, monkeypatch):
    dsn = os.getenv("SCOPE_TEST_DSN")
    if not dsn:
        pytest.skip("SCOPE_TEST_DSN selects an independent test database")
    schema = "ppt_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        dsn, execution_options={"schema_translate_map": {None: schema}}
    )
    async with engine.begin() as c:
        await c.execute(text(f'CREATE SCHEMA "{schema}"'))
        await c.run_sync(lambda conn: MemoryBank.__table__.create(conn))
        await c.run_sync(lambda conn: Document.__table__.create(conn))
    await migrate(engine)
    await migrate(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    binding = ScopeBinding(bank_id="test-bank", subject_id="tester")
    monkeypatch.setattr(settings, "memory_scope_bindings", {"test-authority": binding})
    monkeypatch.setattr(settings.ppt, "enabled", True)
    monkeypatch.setattr(
        settings.image, "base_url", "https://ark.cn-beijing.volces.com/api/plan/v3"
    )
    monkeypatch.setattr(settings.image, "model", "doubao-seedream-5.0-lite")
    monkeypatch.setattr(settings.image, "api_key", "fixture-only")
    async with sessions() as s, s.begin():
        s.add(MemoryBank(id="test-bank", name="test"))
    store = PPTStore(sessions, tmp_path)
    spec = DeckSpec(
        title="test",
        style="navy",
        pages=[
            PageSpec(
                title=f"page {i}", points=["120万元"], layout="timeline", notes="notes"
            )
            for i in range(2)
        ],
    )
    worker = PPTWorker(store, None, None, None)
    job = await store.create(spec, binding, "test-authority", "session")
    ctx = SimpleNamespace(
        store=store,
        worker=worker,
        job=job,
        binding=binding,
        spec=spec,
        sessions=sessions,
    )
    try:
        yield ctx
    finally:
        async with engine.begin() as c:
            await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await engine.dispose()


async def control(c, action, **kw):
    await c.store.control(
        c.job, c.binding, "test-authority", kw.pop("revision", 1), action, **kw
    )


async def accept_page(c):
    claim = await c.worker.claim()
    assert claim["kind"] == "image"
    await c.worker.finish(
        claim, {"path": "unused", "sha256": "fixture", "usage": {"total_tokens": 14400}}
    )
    qa = await c.worker.claim()
    assert qa["kind"] == "qa"
    await c.worker.finish(qa, {"passed": True, "usage": {"total_tokens": 100}})


async def test_approval_gates_and_stale_revision(context):
    c = context
    assert await c.worker.claim() is None
    with pytest.raises(ValueError, match="Stale"):
        await control(c, "approve_outline", revision=2)
    await control(c, "approve_outline")
    with pytest.raises(ValueError):
        await control(c, "approve_sample")
    await accept_page(c)
    assert await c.worker.claim() is None
    await control(c, "approve_sample")
    claim = await c.worker.claim()
    assert claim["page"] == 2


async def test_two_workers_reserve_only_once_and_cancel_late_result(context):
    c = context
    await control(c, "approve_outline")
    import asyncio

    claims = await asyncio.gather(
        c.worker.claim(), PPTWorker(c.store, None, None, None).claim()
    )
    assert sum(x is not None for x in claims) == 1
    claim = next(x for x in claims if x)
    await control(c, "cancel")
    await c.worker.finish(claim, {"usage": {"total_tokens": 14400}})
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["status"] == "cancelled"
    assert state["accounting"]["image_attempts"] == 1
    assert state["accounting"]["tokens"] == 14400
    assert await c.worker.claim() is None


async def test_unknown_outcome_blocks_retries_and_fences_old_worker(context):
    c = context
    await control(c, "approve_outline")
    claim = await c.worker.claim()
    async with c.sessions() as s, s.begin():
        page = await s.get(PPTPage, (c.job, 1))
        page.expires_at = now() - timedelta(seconds=1)
    assert await c.worker.claim() is None
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["pages"][0]["status"] == "unknown"
    await control(c, "retry", page=1)
    replacement = await c.worker.claim()
    assert replacement["lease"] != claim["lease"]
    assert await c.worker.finish(claim, {"usage": {"total_tokens": 10}}) is False


async def test_recovery_after_file_publication_avoids_second_image_call(context):
    c = context
    await control(c, "approve_outline")
    claim = await c.worker.claim()
    folder = c.store.path(f"{c.job}/{claim['lease']}")
    folder.mkdir(parents=True)
    image = folder / "slide.png"
    image.write_bytes(b"fixture image")
    record = {
        "path": str(image.relative_to(c.store.root)),
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "usage": {"total_tokens": 14400},
    }
    (folder / "result.json").write_text(json.dumps(record))
    async with c.sessions() as s, s.begin():
        (await s.get(PPTPage, (c.job, 1))).expires_at = now() - timedelta(seconds=1)
    recovered = await PPTWorker(c.store, None, None, None).claim()
    assert recovered["kind"] == "qa"
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["accounting"]["image_attempts"] == 1


async def test_budget_pauses_before_dispatch_and_unknown_cost_is_not_zero(context):
    c = context
    async with c.sessions() as s, s.begin():
        job = await s.get(PPTJob, c.job)
        job.budget = Budget(image_attempts=4, tokens=10).model_dump()
    await control(c, "approve_outline")
    assert await c.worker.claim() is None
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["status"] == "paused_budget" and not state["accounting"]
    await control(c, "budget", budget=Budget(image_attempts=4, afp=100))
    assert await c.worker.claim() is None
    assert state["billing"]["microusd"] is None


async def test_cross_bank_and_revoked_authority_are_denied(context, monkeypatch):
    c = context
    with pytest.raises(PermissionError):
        await c.store.get(c.job, ScopeBinding(bank_id="other"), "other")
    await control(c, "approve_outline")
    monkeypatch.setattr(settings, "memory_scope_bindings", {})
    assert await c.worker.claim() is None
    with pytest.raises(PermissionError):
        await c.store.get(c.job, c.binding, "test-authority")


async def test_source_edit_blocks_generation(context):
    c = context
    docid = uuid.uuid4()
    async with c.sessions() as s, s.begin():
        s.add(
            Document(
                id=docid,
                bank_id="test-bank",
                title="source",
                raw_text="original",
                file_type="markdown",
            )
        )
    revised = c.spec.model_copy(update={"source_document_ids": [str(docid)]})
    await control(c, "revise", spec=revised)
    await control(c, "approve_outline", revision=2)
    async with c.sessions() as s, s.begin():
        (await s.get(Document, docid)).raw_text = "changed"
    assert await c.worker.claim() is None
    with pytest.raises(PermissionError, match="source"):
        await c.store.get(c.job, c.binding, "test-authority")


async def test_single_page_revision_reuses_other_page_and_style_invalidates_all(
    context,
):
    c = context
    await control(c, "approve_outline")
    await accept_page(c)
    await control(c, "approve_sample")
    await accept_page(c)
    revised = c.spec.model_copy(deep=True)
    revised.pages[1].title = "changed page"
    await control(c, "revise", spec=revised)
    async with c.sessions() as s:
        pages = (
            await s.scalars(
                select(PPTPage).where(PPTPage.job_id == c.job).order_by(PPTPage.number)
            )
        ).all()
        assert [p.status for p in pages] == ["accepted", "pending"]
    revised.style = "orange"
    await control(c, "revise", revision=2, spec=revised)
    async with c.sessions() as s:
        pages = (await s.scalars(select(PPTPage).where(PPTPage.job_id == c.job))).all()
        assert all(p.status == "pending" for p in pages)


async def test_full_worker_restart_and_cross_job_cache(context):
    c = context
    calls = []

    class Provider:
        async def generate(self, prompt, *, references):
            calls.append(prompt)
            data = prompt.encode()
            return SimpleNamespace(
                data=data,
                mime="image/png",
                sha256=hashlib.sha256(data).hexdigest(),
                requested_model="fixture",
                actual_model="fixture",
                request_id="fixture",
                usage={"total_tokens": 14400},
            )

    async def review(claim, references):
        return {"passed": True, "usage": {"total_tokens": 100}}

    def assemble(job, pages):
        assert len(pages) == 2 and all(p.status == "accepted" for p in pages)
        return {"path": "fixture.pptx", "sha256": "fixture"}

    worker = PPTWorker(c.store, Provider(), review, assemble)
    await control(c, "approve_outline")
    await worker.run_once()
    worker = PPTWorker(c.store, Provider(), review, assemble)
    await worker.run_once()
    assert len(calls) == 1
    await control(c, "approve_sample")
    for _ in range(3):
        await worker.run_once()
    assert (await c.store.get(c.job, c.binding, "test-authority"))[
        "status"
    ] == "completed"
    assert len(calls) == 2
    other = await c.store.create(c.spec, c.binding, "test-authority", "second-session")
    await c.store.control(other, c.binding, "test-authority", 1, "approve_outline")
    await worker.run_once()
    state = await c.store.get(other, c.binding, "test-authority")
    assert state["pages"][0]["status"] == "accepted"
    assert not state["accounting"] and len(calls) == 2


async def test_bank_policy_revocation_blocks_preview(context):
    c = context
    async with c.sessions() as s, s.begin():
        (await s.get(MemoryBank, "test-bank")).policy_version = 2
    with pytest.raises(PermissionError, match="policy"):
        await c.store.file(c.job, c.binding, "test-authority", page=1)


async def test_visual_failure_gets_only_one_automatic_repair(context):
    c = context
    await control(c, "approve_outline")
    for _ in range(2):
        image = await c.worker.claim()
        await c.worker.finish(
            image,
            {"path": "unused", "sha256": "fixture", "usage": {"total_tokens": 14400}},
        )
        qa = await c.worker.claim()
        await c.worker.finish(
            qa, {"passed": False, "reason": "金额错误", "usage": {"total_tokens": 100}}
        )
    assert await c.worker.claim() is None
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["status"] == "failed" and state["artifact"] is None
    assert state["accounting"]["image_attempts"] == 2


async def test_cancel_during_provider_call_stops_remaining_pages(context):
    c = context
    calls = []

    class Provider:
        async def generate(self, prompt, *, references):
            calls.append(prompt)
            await control(c, "cancel")
            return SimpleNamespace(
                data=b"late",
                mime="image/png",
                sha256=hashlib.sha256(b"late").hexdigest(),
                requested_model="fixture",
                actual_model="fixture",
                request_id="fixture",
                usage={"total_tokens": 14400},
            )

    await control(c, "approve_outline")
    worker = PPTWorker(c.store, Provider(), None, None)
    await worker.run_once()
    assert await worker.run_once() is False
    state = await c.store.get(c.job, c.binding, "test-authority")
    assert state["status"] == "cancelled" and state["artifact"] is None
    assert len(calls) == 1 and state["accounting"]["tokens"] == 14400
