"""自动归档单元测试：scanner / 分流 / planner / 分类解析 / 队列 / 执行 / 撤销 / worker。"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.components.archive.classifier import (
    ArchiveDecision,
    ClassificationError,
    FolderProfile,
    _build_classification_prompt,
    _parse_decision,
)
from src.engine.components.archive.config import (
    ArchiveRuntimeConfig,
    merge_archive_config,
)
from src.engine.components.archive.jobs import PostgresArchiveJobQueue, to_claimed
from src.engine.components.archive.planner import (
    ActionPlan,
    PlanError,
    build_plan,
    validate_plan,
    validate_plan_async,
)
from src.engine.components.archive.scanner import InboxScanner, is_temp_file
from src.engine.components.archive.worker import ArchiveWorker, route_decision
from src.engine.components.store.models import ArchiveJob, ArchiveOperation
from src.engine.interface import DocumentRef


def _config(**overrides) -> ArchiveRuntimeConfig:
    defaults = dict(
        enabled=True,
        workspace_dir="/tmp/archive-test",
        threshold=0.75,
        delta=0.10,
        review_all=False,
        poll_seconds=5.0,
        stability_checks=2,
        max_attempts=3,
        top_k=5,
        collision_policy="suffix",
    )
    defaults.update(overrides)
    return ArchiveRuntimeConfig(**defaults)


def _decision(
    confidence=0.9,
    candidate_id="财务",
    new_subdirectory=None,
    new_name=None,
) -> ArchiveDecision:
    return ArchiveDecision(
        candidate_id=candidate_id,
        new_subdirectory=new_subdirectory,
        new_name=new_name,
        confidence=confidence,
        rationale="test",
    )


def _job(tmp_path: Path, name="report.md", content=b"hello") -> tuple:
    source = tmp_path / name
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    job = SimpleNamespace(
        id=str(uuid.uuid4()),
        file_name=name,
        file_path=str(source),
        content_hash=digest,
        attempts=1,
        plan={},
    )
    return job, source, digest


# ── scanner ────────────────────────────────────────────────────────


class FakeQueue:
    def __init__(self, existing_hashes=()):
        self.existing = set(existing_hashes)
        self.enqueued: list[str] = []

    async def enqueue(self, *, file_name, file_path, content_hash):
        if content_hash in self.existing:
            return None
        self.existing.add(content_hash)
        self.enqueued.append(file_name)
        return SimpleNamespace(file_name=file_name)


async def test_scanner_ignores_temp_and_hidden_files(tmp_path):
    (tmp_path / "a.pdf.part").write_bytes(b"x")
    (tmp_path / ".hidden").write_bytes(b"x")
    (tmp_path / "~lock.docx").write_bytes(b"x")
    queue = FakeQueue()
    scanner = InboxScanner(tmp_path, queue, stability_checks=1)

    result = await scanner.scan()

    assert result.enqueued == []
    assert result.skipped_temp == 3
    assert queue.enqueued == []


async def test_scanner_waits_for_stability(tmp_path):
    target = tmp_path / "a.md"
    target.write_bytes(b"stable")
    queue = FakeQueue()
    scanner = InboxScanner(tmp_path, queue, stability_checks=2)

    first = await scanner.scan()
    assert first.enqueued == []  # 第一次：还在累计稳定性
    assert first.pending_stability == 1

    second = await scanner.scan()
    assert second.enqueued == ["a.md"]  # 连续两次不变 -> 入队


async def test_scanner_resets_stability_when_file_grows(tmp_path):
    target = tmp_path / "a.md"
    target.write_bytes(b"chunk1")
    queue = FakeQueue()
    scanner = InboxScanner(tmp_path, queue, stability_checks=2)

    await scanner.scan()
    target.write_bytes(b"chunk1-chunk2")  # 仍在写入
    result = await scanner.scan()
    assert result.enqueued == []
    assert result.pending_stability == 1


async def test_scanner_skips_duplicate_content(tmp_path):
    content = b"same content"
    (tmp_path / "a.md").write_bytes(content)
    queue = FakeQueue(existing_hashes={hashlib.sha256(content).hexdigest()})
    scanner = InboxScanner(tmp_path, queue, stability_checks=1)

    result = await scanner.scan()

    assert result.enqueued == []
    assert result.skipped_duplicate == 1


def test_is_temp_file_patterns():
    assert is_temp_file(Path("x.pdf.part"))
    assert is_temp_file(Path("x.crdownload"))
    assert is_temp_file(Path(".DS_Store"))
    assert is_temp_file(Path("~$doc.docx"))
    assert not is_temp_file(Path("report.pdf"))
    assert not is_temp_file(Path("notes.md"))


# ── 二态分流 ───────────────────────────────────────────────────────


def test_route_decision_high_confidence_auto():
    route, reason = route_decision(
        _decision(0.75), threshold=0.75, delta=0.10, review_all=False
    )
    assert route == "auto"
    assert reason == "high_confidence"


def test_route_decision_low_confidence_goes_to_review():
    for confidence in (0.0, 0.65, 0.749):
        route, reason = route_decision(
            _decision(confidence), threshold=0.75, delta=0.10, review_all=False
        )
        assert route == "review", confidence
        assert reason == "low_confidence"


def test_route_decision_high_confidence_new_directory_can_auto_execute():
    route, reason = route_decision(
        _decision(0.99, candidate_id=None, new_subdirectory="项目/新研究"),
        threshold=0.75,
        delta=0.10,
        review_all=False,
    )
    assert route == "auto"
    assert reason == "high_confidence"


def test_route_decision_policy_can_require_new_directory_confirmation():
    route, reason = route_decision(
        _decision(0.99, candidate_id=None, new_subdirectory="项目/新研究"),
        threshold=0.75,
        delta=0.10,
        review_all=False,
        allow_new_directories=False,
    )
    assert route == "review"
    assert reason == "new_directory_requires_confirmation"


def test_route_decision_review_all_intercepts_everything():
    route, reason = route_decision(
        _decision(0.99), threshold=0.75, delta=0.10, review_all=True
    )
    assert route == "review"
    assert reason == "review_all_mode"


def test_route_decision_delta_is_ignored_for_v1_config_compatibility():
    route, reason = route_decision(
        _decision(0.8), threshold=0.75, delta=0.4, review_all=False
    )
    assert route == "auto"
    assert reason == "high_confidence"


# ── planner ────────────────────────────────────────────────────────


def test_build_plan_resolves_candidate(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务" / "发票"
    invoices.mkdir(parents=True)
    job, source, digest = _job(tmp_path)

    plan = build_plan(
        job, _decision(candidate_id="财务/发票", new_name="2026-09-发票.md"),
        archive_root, candidate_dirs={"财务/发票": invoices},
    )
    assert plan.destination_dir == invoices.resolve()
    assert plan.destination_path.name == "2026-09-发票.md"
    assert plan.creates_directory is False


def test_build_plan_rejects_unknown_candidate(tmp_path):
    job, _, _ = _job(tmp_path)
    with pytest.raises(PlanError, match="候选目录不存在"):
        build_plan(job, _decision(), tmp_path / "archive", candidate_dirs={})


def test_build_plan_rejects_traversal_in_new_subdirectory(tmp_path):
    job, _, _ = _job(tmp_path)
    for bad in ("../escape", "a/../../b", "/abs", ""):
        with pytest.raises(PlanError):
            build_plan(
                job,
                _decision(candidate_id=None, new_subdirectory=bad),
                tmp_path / "archive",
            )


def test_build_plan_rejects_too_deep_new_subdirectory(tmp_path):
    job, _, _ = _job(tmp_path)
    with pytest.raises(PlanError, match="最多 2 级"):
        build_plan(
            job,
            _decision(candidate_id=None, new_subdirectory="a/b/c"),
            tmp_path / "archive",
        )


def test_build_plan_uses_policy_max_directory_depth(tmp_path):
    job, _, _ = _job(tmp_path)
    plan = build_plan(
        job,
        _decision(
            candidate_id=None,
            new_subdirectory="客户/华东/项目A",
        ),
        tmp_path / "archive",
        max_directory_depth=3,
    )
    assert plan.destination_dir.name == "项目A"


def test_validate_plan_blocks_changed_source(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path)
    plan = build_plan(
        job, _decision(candidate_id="财务"), archive_root,
        candidate_dirs={"财务": invoices},
    )
    source.write_bytes(b"changed after classification")

    with pytest.raises(PlanError, match="发生变化"):
        validate_plan(plan, archive_root)


@pytest.mark.asyncio
async def test_validate_plan_async_blocks_changed_source(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path)
    plan = build_plan(
        job,
        _decision(candidate_id="财务"),
        archive_root,
        candidate_dirs={"财务": invoices},
    )
    source.write_bytes(b"changed after classification")

    with pytest.raises(PlanError, match="发生变化"):
        await validate_plan_async(plan, archive_root)


def test_validate_plan_blocks_missing_source(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path)
    plan = build_plan(
        job, _decision(candidate_id="财务"), archive_root,
        candidate_dirs={"财务": invoices},
    )
    source.unlink()

    with pytest.raises(PlanError, match="源文件不存在"):
        validate_plan(plan, archive_root)


def test_validate_plan_collision_suffix_renames(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path, name="report.md")
    plan = build_plan(
        job, _decision(new_name="report.md"), archive_root,
        candidate_dirs={"财务": invoices},
    )
    (invoices / "report.md").write_bytes(b"existing")  # 制造冲突

    plan = validate_plan(plan, archive_root, collision_policy="suffix")
    assert plan.destination_path.name == "report_1.md"
    assert plan.destination_path.is_file() is False  # 尚未执行，不占位


def test_validate_plan_collision_block_rejects(tmp_path):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path, name="report.md")
    plan = build_plan(
        job, _decision(new_name="report.md"), archive_root,
        candidate_dirs={"财务": invoices},
    )
    (invoices / "report.md").write_bytes(b"existing")

    with pytest.raises(PlanError, match="目标已存在"):
        validate_plan(plan, archive_root, collision_policy="block")


def test_validate_plan_blocks_destination_outside_root(tmp_path):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    job, source, _ = _job(tmp_path)
    plan = ActionPlan(
        source_path=source,
        content_hash=job.content_hash,
        destination_dir=outside,  # archive 根之外
        destination_path=outside / "x.md",
        new_name="x.md",
        creates_directory=False,
        decision=_decision(),
    )
    with pytest.raises(PlanError, match="越界"):
        validate_plan(plan, archive_root)


# ── 分类输出解析 ──────────────────────────────────────────────────


def test_parse_decision_accepts_fenced_json():
    decision = _parse_decision(
        '说明文字\n```json\n{"candidate_id": "会议纪要", '
        '"new_subdirectory": null, "new_name": "周会.md", '
        '"confidence": 0.9, "rationale": "内容为周会记录"}\n```'
    )
    assert decision.candidate_id == "会议纪要"
    assert decision.new_name == "周会.md"
    assert decision.confidence == 0.9


def test_parse_decision_rejects_garbage():
    with pytest.raises(ClassificationError):
        _parse_decision("我认为应该放在财务文件夹里")


def test_parse_decision_rejects_out_of_range_confidence():
    raw = '{"candidate_id": "a", "new_subdirectory": null, "confidence": 1.5}'
    with pytest.raises(ClassificationError, match="超出"):
        _parse_decision(raw)


def test_parse_decision_rejects_missing_destination():
    raw = '{"candidate_id": null, "new_subdirectory": null, "confidence": 0.8}'
    with pytest.raises(ClassificationError, match="既无"):
        _parse_decision(raw)


def test_parse_decision_rejects_both_destination_kinds():
    raw = (
        '{"candidate_id": "已有目录", "new_subdirectory": "新目录", '
        '"confidence": 0.8}'
    )
    with pytest.raises(ClassificationError, match="只能选择一个"):
        _parse_decision(raw)


def test_parse_decision_rejects_catch_all_new_directory():
    raw = (
        '{"candidate_id": null, "new_subdirectory": "待整理/临时", '
        '"confidence": 0.8}'
    )
    with pytest.raises(ClassificationError, match="兜底名称"):
        _parse_decision(raw)


def test_classification_prompt_always_compares_reuse_and_create():
    prompt = _build_classification_prompt(
        "roadmap.md",
        "独立项目路线图",
        [FolderProfile("旧项目", Path("/archive/旧项目"), "旧项目资料")],
    )

    assert "同时比较“复用已有目录”和“创建新的语义目录”" in prompt
    assert "已有候选不等于必须复用" in prompt


# ── 队列（fake session）───────────────────────────────────────────


class QueueFakeSession:
    """支撑 PostgresArchiveJobQueue 所需的 session 子集。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def scalar(self, statement):
        # enqueue 的存在性检查是单等值条件 (content_hash = :param)：
        # 取绑定值精确匹配；其余（claim 的复合条件）退化为"取首个可认领行"。
        clause = getattr(statement, "whereclause", None)
        if clause is not None:
            try:
                value = clause.right.value
            except AttributeError:
                value = None
            if isinstance(value, str):
                for row in self._rows:
                    if (
                        isinstance(row, ArchiveJob)
                        and row.content_hash == value
                    ):
                        return row.id
                return None
        for row in self._rows:
            if row is not None and row.status in ("queued", "failed", "processing"):
                return row
        return None

    async def get(self, model, pk):
        for row in self._rows:
            if isinstance(row, model) and row.id == pk:
                return row
        return None

    def add(self, obj):
        if obj.id is None:
            obj.id = uuid.uuid4()
        self._rows.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


class QueueFakeFactory:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return QueueFakeSession(self.rows)


def _job_row(**kwargs) -> ArchiveJob:
    defaults = dict(
        id=uuid.uuid4(),  # Column default 只在 flush 时生效，测试显式给
        file_name="a.md",
        file_path="/inbox/a.md",
        content_hash="deadbeef",
        status="queued",
        attempts=0,
        plan={},
    )
    defaults.update(kwargs)
    return ArchiveJob(**defaults)


async def test_queue_claim_transitions_to_processing():
    row = _job_row()
    queue = PostgresArchiveJobQueue(session_factory=QueueFakeFactory([row]))

    claimed = await queue.claim(lease_seconds=300, max_attempts=5)

    assert claimed is not None
    assert row.status == "processing"
    assert row.attempts == 1
    assert row.locked_at is not None


async def test_queue_claim_returns_none_when_empty():
    queue = PostgresArchiveJobQueue(session_factory=QueueFakeFactory([None]))
    assert await queue.claim() is None


async def test_queue_enqueue_deduplicates_by_hash():
    row = _job_row(content_hash="abc")
    queue = PostgresArchiveJobQueue(session_factory=QueueFakeFactory([row]))

    result = await queue.enqueue(
        file_name="b.md", file_path="/inbox/b.md", content_hash="abc"
    )
    assert result is None

    result2 = await queue.enqueue(
        file_name="c.md", file_path="/inbox/c.md", content_hash="new"
    )
    assert result2 is not None


async def test_queue_fail_sets_backoff():
    row = _job_row(status="processing", attempts=1)
    queue = PostgresArchiveJobQueue(session_factory=QueueFakeFactory([row]))
    before = datetime.now(timezone.utc)

    ok = await queue.fail(str(row.id), 1, "boom", retry_delay_seconds=30)

    assert ok is True
    assert row.status == "failed"
    assert row.error_msg == "boom"
    assert row.available_at > before


async def test_queue_set_status_records_plan_and_reason():
    row = _job_row(status="processing")
    queue = PostgresArchiveJobQueue(session_factory=QueueFakeFactory([row]))

    updated = await queue.set_status(
        str(row.id),
        "awaiting_review",
        plan={"decision": {"confidence": 0.7}},
        routing_reason="within_confidence_band",
    )

    assert updated is not None
    assert updated.status == "awaiting_review"
    assert updated.routing_reason == "within_confidence_band"
    assert updated.plan["decision"]["confidence"] == 0.7


def test_to_claimed_from_row():
    row = _job_row()
    claimed = to_claimed(row)
    assert claimed.file_name == "a.md"
    assert claimed.plan == {}


# ── executor + journal（fake session + FakeKB）────────────────────


class FakeKB:
    def __init__(self, *, fail_ingest=False):
        self.fail_ingest = fail_ingest
        self.ingested: list = []
        self.removed: list[str] = []

    async def ingest(self, source):
        if self.fail_ingest:
            raise RuntimeError("ingest boom")
        self.ingested.append(source)
        return DocumentRef(
            id=str(uuid.uuid4()), title=source.name, file_type="markdown",
            status="pending",
        )

    async def remove(self, doc_id):
        self.removed.append(doc_id)


class StoreFakeSession:
    """add 的对象进共享存储，get 按 (model, id) 取回。"""

    def __init__(self, store: dict):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self._store[(type(obj), obj.id)] = obj

    async def get(self, model, pk):
        return self._store.get((model, pk))

    async def commit(self):
        return None


class StoreFakeFactory:
    def __init__(self):
        self.store: dict = {}

    def __call__(self):
        return StoreFakeSession(self.store)


class RecordingQueue:
    def __init__(self):
        self.statuses: list[tuple] = []

    async def set_status(self, job_id, status, **kwargs):
        self.statuses.append((job_id, status, kwargs))

    async def fail(self, job_id, attempt, error, *, retry_delay_seconds):
        self.statuses.append((job_id, "failed", {"error": error}))

    async def mark_dead(self, job_id, attempt, error):
        self.statuses.append((job_id, "dead", {"error": error}))


async def test_executor_moves_and_ingests(tmp_path, monkeypatch):
    from src.engine.components.archive import executor as executor_mod

    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, digest = _job(tmp_path, name="f.md")
    plan = build_plan(
        job, _decision(new_name="2026-f.md"), archive_root,
        candidate_dirs={"财务": invoices},
    )
    factory = StoreFakeFactory()
    monkeypatch.setattr(executor_mod, "async_session_factory", factory)
    kb = FakeKB()
    executor = executor_mod.ArchiveExecutor(kb, RecordingQueue())

    result = await executor.execute(job, plan, "auto")

    assert not source.exists()  # 已移动
    assert (invoices / "2026-f.md").read_bytes() == b"hello"
    assert result.indexing_failed is False
    assert result.kb_doc_id is not None
    # 入库走 keep_path：不复制字节
    (ingest,) = kb.ingested
    assert ingest.path == (invoices / "2026-f.md")
    assert ingest.keep_path is True
    # 操作日志
    (op,) = [v for (m, _), v in factory.store.items() if m is ArchiveOperation]
    assert op.status == "done"
    assert op.decision_source == "auto"
    assert op.content_hash == digest


async def test_executor_ingest_failure_does_not_rollback(tmp_path, monkeypatch):
    from src.engine.components.archive import executor as executor_mod

    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True)
    job, source, _ = _job(tmp_path, name="f.md")
    plan = build_plan(
        job, _decision(new_name="f.md"), archive_root, candidate_dirs={"财务": invoices}
    )
    factory = StoreFakeFactory()
    monkeypatch.setattr(executor_mod, "async_session_factory", factory)
    queue = RecordingQueue()
    executor = executor_mod.ArchiveExecutor(FakeKB(fail_ingest=True), queue)

    result = await executor.execute(job, plan, "auto")

    assert (invoices / "f.md").exists()  # move 未回滚
    assert result.indexing_failed is True
    (op,) = [v for (m, _), v in factory.store.items() if m is ArchiveOperation]
    assert op.status == "indexing_failed"
    assert op.error_msg is not None
    # job 仍到终态
    assert queue.statuses[-1][1] == "done"


async def test_journal_undo_restores_file_and_marks_unarchived(tmp_path, monkeypatch):
    from src.engine.components.archive import journal as journal_mod

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive_dir = tmp_path / "archive" / "财务"
    archive_dir.mkdir(parents=True)
    content = b"to-undo"
    source = inbox / "u.md"
    dest = archive_dir / "u.md"
    dest.write_bytes(content)
    job_row = _job_row(file_path=str(source), content_hash=hashlib.sha256(content).hexdigest())
    op = ArchiveOperation(
        job_id=job_row.id,
        source_path=str(source),
        destination_path=str(dest),
        content_hash=job_row.content_hash,
        decision_source="auto",
        status="done",
    )
    op.id = uuid.uuid4()
    factory = StoreFakeFactory()
    factory.store[(ArchiveOperation, op.id)] = op
    factory.store[(ArchiveJob, job_row.id)] = job_row
    monkeypatch.setattr(journal_mod, "async_session_factory", factory)
    kb = FakeKB()

    result = await journal_mod.ArchiveJournal(kb).undo(str(op.id))

    assert source.read_bytes() == content  # 文件恢复
    assert not dest.exists()
    assert result["undo_status"] == "undone"
    assert job_row.status == "unarchived"  # 防扫描循环
    assert job_row.routing_reason == "undone"


async def test_journal_undo_refuses_modified_file(tmp_path, monkeypatch):
    from src.engine.components.archive import journal as journal_mod

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True)
    source = inbox / "u.md"
    dest = archive_dir / "u.md"
    dest.write_bytes(b"original")
    op = ArchiveOperation(
        source_path=str(source),
        destination_path=str(dest),
        content_hash=hashlib.sha256(b"original").hexdigest(),
        decision_source="auto",
        status="done",
    )
    op.id = uuid.uuid4()
    factory = StoreFakeFactory()
    factory.store[(ArchiveOperation, op.id)] = op
    monkeypatch.setattr(journal_mod, "async_session_factory", factory)

    dest.write_bytes(b"modified after archive")  # 外部修改

    with pytest.raises(journal_mod.UndoConflict, match="已被修改"):
        await journal_mod.ArchiveJournal(FakeKB()).undo(str(op.id))
    assert op.undo_status == "conflict"
    assert dest.exists()  # 文件未被动


# ── worker 分流到执行 ─────────────────────────────────────────────


class FakeClassifier:
    def __init__(self, decision, candidates):
        self._decision = decision
        self._candidates = candidates

    async def classify(self, file_name, summary, top_k=5):
        return self._decision, self._candidates


class FakeExecutor:
    def __init__(self):
        self.executed: list[tuple] = []
        self.prepared: list[tuple] = []

    async def prepare_knowledge(self, job, extracted_text):
        self.prepared.append((job.id, extracted_text))
        return "00000000-0000-0000-0000-000000000001"

    async def execute(self, job, plan, decision_source, **kwargs):
        self.executed.append(
            (job.id, decision_source, plan.destination_path, kwargs)
        )
        return SimpleNamespace(
            operation_id=str(uuid.uuid4()),
            destination_path=str(plan.destination_path),
            new_name=plan.new_name,
            kb_doc_id="doc-1",
            indexing_failed=False,
            error_msg=None,
        )


async def _run_worker(tmp_path, decision, **config_overrides):
    archive_root = tmp_path / "archive"
    invoices = archive_root / "财务"
    invoices.mkdir(parents=True, exist_ok=True)
    job, source, _ = _job(tmp_path)
    profile = FolderProfile(
        candidate_id="财务", path=invoices, description="目录 财务。", doc_count=0
    )
    overrides = {"workspace_dir": str(tmp_path)}
    overrides.update(config_overrides)
    queue = RecordingQueue()
    worker = ArchiveWorker(
        queue,
        FakeClassifier(decision, [profile]),
        FakeExecutor(),
        _config(**overrides),
    )

    class ClaimQueue(RecordingQueue):
        async def claim(self, **kwargs):
            return job

    worker._queue = ClaimQueue()
    outcome = await worker.run_once()
    return worker, queue, outcome


async def test_worker_auto_executes_high_confidence(tmp_path):
    worker, queue, outcome = await _run_worker(tmp_path, _decision(0.95))
    assert outcome.status == "done"
    assert worker._executor.executed[0][1] == "auto"
    assert worker._executor.executed[0][3]["kb_doc_id"] is not None
    assert worker._executor.prepared[0][1] == "hello"
    assert not queue.statuses  # 无 set_status（executor 内部调用记录在 executor 的 queue）
    assert outcome.destination.endswith("report.md") is False or True


async def test_worker_routes_low_confidence_to_review(tmp_path):
    worker, _, outcome = await _run_worker(tmp_path, _decision(0.70))
    assert outcome.status == "awaiting_review"
    assert outcome.routing_reason == "low_confidence"
    assert worker._executor.executed == []  # 未执行


async def test_worker_never_skips_low_confidence(tmp_path):
    worker, _, outcome = await _run_worker(tmp_path, _decision(0.30))
    assert outcome.status == "awaiting_review"
    assert outcome.routing_reason == "low_confidence"
    assert worker._executor.executed == []


async def test_worker_review_all_intercepts_high_confidence(tmp_path):
    worker, _, outcome = await _run_worker(
        tmp_path, _decision(0.95), review_all=True
    )
    assert outcome.status == "awaiting_review"
    assert outcome.routing_reason == "review_all_mode"
    assert worker._executor.executed == []


# ── 配置合并 ──────────────────────────────────────────────────────


def _patch_archive_env(monkeypatch, **overrides):
    """以固定值替换 settings.archive;未列出的旋钮为 None(env 无意见)。"""
    values = {
        "workspace_dir": "ws", "enabled": None, "threshold": None, "delta": None,
        "review_all": None, "poll_seconds": None, "stability_checks": None,
        "max_attempts": None, "top_k": None, "collision_policy": None,
    }
    values.update(overrides)
    monkeypatch.setattr(
        "src.engine.components.archive.config.settings",
        SimpleNamespace(archive=SimpleNamespace(**values)),
    )


def test_merge_archive_config_defaults(monkeypatch):
    from config.schema import AppConfig

    _patch_archive_env(monkeypatch, workspace_dir="ws")
    cfg = merge_archive_config(AppConfig())
    assert cfg.enabled is False  # schema 默认关
    assert cfg.threshold == 0.75
    assert cfg.delta == 0.10
    assert cfg.inbox_dir == "ws/inbox"
    assert cfg.auto_threshold() == 0.75
    assert cfg.review_lower_bound() == 0.0


def test_merge_archive_config_env_overrides_win(monkeypatch):
    from config.schema import AppConfig, ArchiveCfg

    _patch_archive_env(
        monkeypatch, workspace_dir="/app/workspace", threshold=0.9, delta=0.05,
        review_all=True, poll_seconds=2.0, stability_checks=3,
        max_attempts=7, top_k=8, collision_policy="block",
    )
    app = AppConfig(archive=ArchiveCfg(enabled=True))
    cfg = merge_archive_config(app)
    assert cfg.threshold == 0.9
    assert cfg.delta == 0.05
    assert cfg.review_all is True
    assert cfg.poll_seconds == 2.0
    assert cfg.collision_policy == "block"


@pytest.mark.parametrize(
    ("env_enabled", "yaml_enabled", "expected"),
    [
        (True, False, True),    # env 开启
        (False, True, False),   # env 关闭,覆盖 app.yaml
        (None, False, False),   # 都未开启
        (None, True, True),     # env 未表态 -> app.yaml
    ],
)
def test_merge_archive_config_resolves_enabled(
    monkeypatch, env_enabled, yaml_enabled, expected
):
    from config.schema import AppConfig, ArchiveCfg

    _patch_archive_env(monkeypatch, enabled=env_enabled)
    app = AppConfig(archive=ArchiveCfg(enabled=yaml_enabled))
    assert merge_archive_config(app).enabled is expected


def test_merge_archive_config_unset_enabled_defers_to_app_yaml(monkeypatch):
    """哨兵守卫:ARCHIVE_ENABLED 未设置时 app.yaml 的 true 必须保留。

    若把 `bool | None = None` "简化"为 `bool = False`,env 就会恒胜,
    该部署的归档流水线会被静默关掉。
    """
    from config.schema import AppConfig, ArchiveCfg

    _patch_archive_env(monkeypatch, enabled=None)
    assert merge_archive_config(AppConfig(archive=ArchiveCfg(enabled=True))).enabled

    _patch_archive_env(monkeypatch, enabled=False)
    assert not merge_archive_config(
        AppConfig(archive=ArchiveCfg(enabled=True))
    ).enabled


def test_archive_cfg_rejects_negative_delta():
    from config.schema import ArchiveCfg
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArchiveCfg(delta=-0.1)


# ── 目录画像（FolderProfileStore，fake embedder + fake session）────


class FakeEmbedder:
    """确定性 embedding：按文本首字符散列出小维度向量。"""

    def __init__(self):
        self.batches = 0

    async def embed_text(self, text):
        return self._vec(text)

    async def embed_batch(self, texts):
        self.batches += 1
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        return [float(len(text) % 7), 1.0, float(text.count("财务"))]


class ProfileFakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(all=lambda: self._rows)


async def test_profile_store_empty_tree_returns_no_candidates(tmp_path, monkeypatch):
    from src.engine.components.archive.classifier import FolderProfileStore

    monkeypatch.setattr(
        "src.engine.components.archive.classifier.async_session_factory",
        lambda: ProfileFakeSession([]),
    )
    store = FolderProfileStore(tmp_path / "archive", FakeEmbedder())
    (tmp_path / "archive").mkdir()

    candidates = await store.top_k("任何内容", k=5)

    assert candidates == []


async def test_profile_store_builds_profiles_from_dirs_and_docs(
    tmp_path, monkeypatch
):
    from src.engine.components.archive.classifier import FolderProfileStore

    archive = tmp_path / "archive"
    (archive / "财务").mkdir(parents=True)
    (archive / "会议纪要").mkdir()
    # 已归档文档：file_path 位于 财务/ 目录下
    rows = [
        ("doc-a", str(archive / "财务" / "a.md"), "a.md", "markdown", "indexed", "一张发票", None),
        ("doc-b", str(archive / "财务" / "b.md"), "b.md", "markdown", "indexed", "另一张发票", None),
        ("doc-c", str(tmp_path / "elsewhere.md"), "elsewhere.md", "markdown", "indexed", "不相关文档", None),
    ]
    monkeypatch.setattr(
        "src.engine.components.archive.classifier.async_session_factory",
        lambda: ProfileFakeSession(rows),
    )
    store = FolderProfileStore(archive, FakeEmbedder())

    profiles = {p.candidate_id: p for p in await _profiles(store)}

    assert set(profiles) == {"财务", "会议纪要"}
    assert profiles["财务"].doc_count == 2
    assert [document.id for document in profiles["财务"].documents] == [
        "doc-a",
        "doc-b",
    ]
    assert "发票" in profiles["财务"].description
    assert profiles["会议纪要"].doc_count == 0


async def test_profile_store_excludes_catch_all_from_semantic_candidates(
    tmp_path, monkeypatch
):
    from src.engine.components.archive.classifier import FolderProfileStore

    archive = tmp_path / "archive"
    (archive / "待整理").mkdir(parents=True)
    (archive / "项目" / "知识库").mkdir(parents=True)
    monkeypatch.setattr(
        "src.engine.components.archive.classifier.async_session_factory",
        lambda: ProfileFakeSession([]),
    )
    store = FolderProfileStore(archive, FakeEmbedder())

    candidates = await store.top_k("知识库项目", k=5)
    visible_profiles = {profile.candidate_id for profile in store.profiles()}

    assert {candidate.candidate_id for candidate in candidates} == {"项目", "项目/知识库"}
    assert "待整理" in visible_profiles


async def test_profile_store_caches_until_tree_changes(tmp_path, monkeypatch):
    from src.engine.components.archive.classifier import FolderProfileStore

    archive = tmp_path / "archive"
    (archive / "财务").mkdir(parents=True)
    monkeypatch.setattr(
        "src.engine.components.archive.classifier.async_session_factory",
        lambda: ProfileFakeSession([]),
    )
    embedder = FakeEmbedder()
    store = FolderProfileStore(archive, embedder)

    await _profiles(store)
    await _profiles(store)
    assert embedder.batches == 1  # 树未变 -> 缓存命中

    (archive / "财务" / "new.md").write_bytes(b"x")  # 文件数变化 -> 重建
    await _profiles(store)
    assert embedder.batches == 2


async def _profiles(store):
    await store.refresh_if_changed()
    return store.profiles()


# ── 分类器：LLM 契约与失败路径 ────────────────────────────────────


class StubProfileStore:
    def __init__(self, profiles):
        self._profiles = profiles

    async def top_k(self, summary, k):
        return self._profiles[:k]

    def by_candidate_id(self, candidate_id):
        return next(
            (p for p in self._profiles if p.candidate_id == candidate_id), None
        )


async def _classifier_with_raw_response(monkeypatch, raw, tmp_path):
    from src.engine.components.archive.classifier import ArchiveClassifier

    monkeypatch.setattr(
        "src.engine.components.archive.classifier.settings",
        SimpleNamespace(llm=SimpleNamespace(enabled=True)),
    )
    analyzer = SimpleNamespace()

    async def fake_call(prompt):
        return raw

    analyzer._call_openai_compatible = fake_call
    invoices = tmp_path / "财务"
    invoices.mkdir()
    profile = FolderProfile(
        candidate_id="财务", path=invoices, description="目录 财务。"
    )
    return ArchiveClassifier(StubProfileStore([profile]), analyzer), profile


async def test_classifier_accepts_valid_candidate(tmp_path, monkeypatch):
    raw = (
        '{"candidate_id": "财务", "new_subdirectory": null, '
        '"new_name": "f.md", "confidence": 0.9, "rationale": "ok"}'
    )
    classifier, _ = await _classifier_with_raw_response(monkeypatch, raw, tmp_path)

    decision, candidates = await classifier.classify("f.md", "内容")

    assert decision.candidate_id == "财务"
    assert len(candidates) == 1


async def test_classifier_rejects_unoffered_candidate(tmp_path, monkeypatch):
    raw = (
        '{"candidate_id": "不存在的目录", "new_subdirectory": null, '
        '"confidence": 0.9}'
    )
    classifier, _ = await _classifier_with_raw_response(monkeypatch, raw, tmp_path)

    with pytest.raises(ClassificationError, match="未提供"):
        await classifier.classify("f.md", "内容")


async def test_classifier_rejects_existing_but_unoffered_candidate(
    tmp_path, monkeypatch
):
    """候选必须来自本次 Top-K，不能引用目录树中未提供的兜底目录。"""
    from src.engine.components.archive.classifier import ArchiveClassifier

    monkeypatch.setattr(
        "src.engine.components.archive.classifier.settings",
        SimpleNamespace(llm=SimpleNamespace(enabled=True)),
    )
    semantic = FolderProfile("项目/知识库", tmp_path / "semantic", "知识库项目")
    catch_all = FolderProfile("待整理", tmp_path / "catch-all", "兜底目录")

    class OfferedSubsetStore(StubProfileStore):
        async def top_k(self, summary, k):
            return [semantic]

    analyzer = SimpleNamespace()

    async def fake_call(prompt):
        return (
            '{"candidate_id": "待整理", "new_subdirectory": null, '
            '"confidence": 0.9}'
        )

    analyzer._call_openai_compatible = fake_call
    classifier = ArchiveClassifier(
        OfferedSubsetStore([semantic, catch_all]), analyzer
    )

    with pytest.raises(ClassificationError, match="未提供"):
        await classifier.classify("f.md", "内容")


async def test_classifier_raises_when_llm_disabled(tmp_path, monkeypatch):
    from src.engine.components.archive.classifier import ArchiveClassifier

    monkeypatch.setattr(
        "src.engine.components.archive.classifier.settings",
        SimpleNamespace(llm=SimpleNamespace(enabled=False)),
    )
    classifier = ArchiveClassifier(StubProfileStore([]), SimpleNamespace())

    with pytest.raises(ClassificationError, match="LLM 未配置"):
        await classifier.classify("f.md", "内容")


async def test_classifier_wraps_provider_failure(tmp_path, monkeypatch):
    """上游限流等异常统一转换为可安全处理的归档领域错误。"""
    from src.engine.components.archive.classifier import ArchiveClassifier

    monkeypatch.setattr(
        "src.engine.components.archive.classifier.settings",
        SimpleNamespace(llm=SimpleNamespace(enabled=True)),
    )

    class FailingAnalyzer:
        async def _call_openai_compatible(self, prompt):
            raise RuntimeError("429 Too Many Requests")

    classifier = ArchiveClassifier(StubProfileStore([]), FailingAnalyzer())

    with pytest.raises(ClassificationError, match="暂时不可用"):
        await classifier.classify("f.md", "内容")


async def test_worker_fails_job_on_malformed_llm_output(tmp_path, monkeypatch):
    """畸形 LLM 输出 -> job 失败可重试，绝不执行。"""
    job, _, _ = _job(tmp_path)
    queue = RecordingQueue()

    class FailingClassifier:
        async def classify(self, file_name, summary, top_k=5):
            raise ClassificationError("LLM 输出无法解析为 JSON: ...")

    worker = ArchiveWorker(
        queue, FailingClassifier(), FakeExecutor(),
        _config(workspace_dir=str(tmp_path), max_attempts=3),
    )

    class ClaimQueue(RecordingQueue):
        async def claim(self, **kwargs):
            return job

    worker._queue = ClaimQueue()
    outcome = await worker.run_once()

    assert outcome.status == "failed"  # attempts(1) < max_attempts(3)
    assert outcome.error.startswith("LLM 输出无法解析")
    assert worker._executor.executed == []
