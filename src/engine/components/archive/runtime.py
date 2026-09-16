"""归档运行时：scanner + worker 生命周期封装，以及人审操作入口。

生命周期仿 graph_runtime：BFF lifespan 内 start/stop。
人审操作（批准/拒绝/改分类/手动归档/撤销/重试入库）都走与自动路径
相同的 planner 校验 + executor 执行 + journal 记录。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from src.engine.components.analyzer import Analyzer
from src.engine.components.embedder import Embedder, embedder
from src.engine.components.store.models import ArchiveJob, ArchiveOperation
from src.engine.components.store.postgres import async_session_factory
from src.engine.interface import KnowledgeBase

from .classifier import ArchiveClassifier, ArchiveDecision, FolderProfileStore
from .config import ArchiveRuntimeConfig
from .executor import ArchiveExecutor
from .jobs import PostgresArchiveJobQueue, to_claimed
from .journal import ArchiveJournal
from .legacy import LegacyArchiveService
from .planner import ActionPlan, build_plan, validate_plan_async
from .policy import ArchivePolicyStore, policy_to_dict
from .scanner import InboxScanner, ScanResult
from .worker import ArchiveWorker, load_job

logger = logging.getLogger(__name__)

# 待审/未归档/历史列表的单页上限。
_LIST_LIMIT = 100


class ArchiveRuntime:
    def __init__(
        self,
        config: ArchiveRuntimeConfig,
        kb: KnowledgeBase,
        *,
        embedder_client: Embedder | None = None,
    ) -> None:
        self.config = config
        self._kb = kb
        self._workspace = Path(config.workspace_dir)
        self._archive_root = Path(config.archive_root)
        self._inbox = Path(config.inbox_dir)

        self.queue = PostgresArchiveJobQueue()
        self.scanner = InboxScanner(
            self._inbox, self.queue, stability_checks=config.stability_checks
        )
        self.profiles = FolderProfileStore(
            self._archive_root, embedder_client or embedder
        )
        self.classifier = ArchiveClassifier(self.profiles, Analyzer())
        self.policies = ArchivePolicyStore()
        self.executor = ArchiveExecutor(kb, self.queue)
        self.journal = ArchiveJournal(kb)
        self.worker = ArchiveWorker(
            self.queue, self.classifier, self.executor, config, self.policies
        )
        self.legacy = LegacyArchiveService(
            self._archive_root,
            self.classifier,
            self.policies,
            threshold=config.threshold,
            top_k=config.top_k,
        )

        self._scanner_task: asyncio.Task[None] | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    # ── 生命周期 ────────────────────────────────────────────────

    async def start(self) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._archive_root.mkdir(parents=True, exist_ok=True)
        policy = await self.policies.current()
        self.classifier.policy_instructions = (
            str(policy.rules.get("instructions", "")) if policy.enabled else ""
        )
        self._stop_event.clear()
        self._scanner_task = asyncio.create_task(
            self._run_scanner(), name="archive-scanner"
        )
        self._worker_task = asyncio.create_task(
            self._run_worker(), name="archive-worker"
        )
        logger.info("归档运行时已启动: workspace=%s", self._workspace)

    async def stop(self) -> None:
        self._stop_event.set()
        for task in (self._scanner_task, self._worker_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._scanner_task = None
        self._worker_task = None
        logger.info("归档运行时已停止")

    async def _run_scanner(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.scanner.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("归档扫描迭代失败")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.config.poll_seconds
                )
            except TimeoutError:
                pass

    async def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                outcome = await self.worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("归档 worker 迭代失败")
                outcome = None
            if outcome is None:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=1.0
                    )
                except TimeoutError:
                    pass

    # ── 模式 ────────────────────────────────────────────────────

    def mode(self) -> dict:
        return {"review_all": self.worker.review_all}

    def set_mode(self, review_all: bool) -> dict:
        self.worker.review_all = review_all
        return self.mode()

    async def get_policy(self) -> dict:
        return policy_to_dict(await self.policies.current())

    async def update_policy(self, *, enabled: bool, rules: dict) -> dict:
        policy = await self.policies.replace(enabled=enabled, rules=rules)
        self.classifier.policy_instructions = (
            str(policy.rules.get("instructions", "")) if policy.enabled else ""
        )
        return policy_to_dict(policy)

    async def scan_legacy(self) -> list[dict]:
        return await self.legacy.scan()

    async def plan_legacy(self, document_ids: list[str] | None = None) -> dict:
        return await self.legacy.plan(document_ids)

    async def execute_legacy(
        self,
        batch_id: str,
        document_ids: list[str] | None = None,
        overrides: dict[str, dict] | None = None,
    ) -> dict:
        return await self.legacy.execute(batch_id, document_ids, overrides)

    async def scan_now(self) -> ScanResult:
        """供上传路由立即触发一次扫描（否则要等下个轮询周期）。"""
        return await self.scanner.scan()

    # ── 列表 ────────────────────────────────────────────────────

    @staticmethod
    def _job_to_dict(job: ArchiveJob) -> dict:
        return {
            "id": str(job.id),
            "file_name": job.file_name,
            "file_path": job.file_path,
            "status": job.status,
            "attempts": job.attempts,
            "plan": job.plan or {},
            "routing_reason": job.routing_reason,
            "error_msg": job.error_msg,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }

    async def list_reviews(self) -> list[dict]:
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(ArchiveJob)
                    .where(ArchiveJob.status == "awaiting_review")
                    .order_by(ArchiveJob.created_at)
                    .limit(_LIST_LIMIT)
                )
            ).scalars().all()
            return [self._job_to_dict(r) for r in rows]

    async def list_attention(self) -> list[dict]:
        """用户暂不归档 + 失败/超限：留在 inbox 需要人处理的文件。"""
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(ArchiveJob)
                    .where(
                        ArchiveJob.status.in_(
                            ("unarchived", "skipped", "failed", "dead")
                        )
                    )
                    .order_by(ArchiveJob.created_at.desc())
                    .limit(_LIST_LIMIT)
                )
            ).scalars().all()
            return [self._job_to_dict(r) for r in rows]

    async def list_unarchived(self) -> list[dict]:
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(ArchiveJob)
                    .where(ArchiveJob.status == "unarchived")
                    .order_by(ArchiveJob.updated_at.desc())
                    .limit(_LIST_LIMIT)
                )
            ).scalars().all()
            return [self._job_to_dict(r) for r in rows]

    async def list_operations(self) -> list[dict]:
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(ArchiveOperation)
                    .order_by(ArchiveOperation.created_at.desc())
                    .limit(_LIST_LIMIT)
                )
            ).scalars().all()
            return [
                {
                    "id": str(r.id),
                    "job_id": str(r.job_id) if r.job_id else None,
                    "source_path": r.source_path,
                    "destination_path": r.destination_path,
                    "decision_source": r.decision_source,
                    "confidence": r.confidence,
                    "rationale": r.rationale,
                    "policy_version": r.policy_version,
                    "policy_id": str(r.policy_id) if r.policy_id else None,
                    "kb_doc_id": str(r.kb_doc_id) if r.kb_doc_id else None,
                    "status": r.status,
                    "undo_status": r.undo_status,
                    "error_msg": r.error_msg,
                    "created_at": (
                        r.created_at.isoformat() if r.created_at else None
                    ),
                }
                for r in rows
            ]

    async def tree(self) -> list[dict]:
        """目录树 + 画像信息（前端只读浏览）。"""
        await self.profiles.refresh_if_changed()
        return [
            {
                "candidate_id": p.candidate_id,
                "description": p.description,
                "doc_count": p.doc_count,
                "documents": [
                    {
                        "id": document.id,
                        "title": document.title,
                        "file_type": document.file_type,
                        "status": document.status,
                        "overview": document.overview,
                        "created_at": document.created_at,
                    }
                    for document in p.documents
                ],
            }
            for p in self.profiles.profiles()
        ]

    # ── 人审操作 ────────────────────────────────────────────────

    async def _load_reviewable(self, job_id: str) -> ArchiveJob:
        job = await load_job(job_id)
        if job is None:
            raise ValueError(f"job 不存在: {job_id}")
        if job.status != "awaiting_review":
            raise ValueError(f"job 不在待审状态: {job.status}")
        return job

    async def _execute_for_job(
        self,
        job_row: ArchiveJob,
        decision: ArchiveDecision,
        decision_source: str,
    ) -> dict:
        """从 job + 决策重建计划 -> 校验 -> 执行（与人审/自动共用路径）。"""
        claimed = to_claimed(job_row)
        candidate_dirs = await self._candidate_dirs()
        plan = build_plan(
            claimed,
            decision,
            self._archive_root,
            candidate_dirs=candidate_dirs,
            max_directory_depth=int(
                (job_row.plan or {}).get("max_directory_depth", 2)
            ),
        )
        plan = await validate_plan_async(
            plan,
            self._archive_root,
            collision_policy=self.config.collision_policy,
        )
        kb_doc_id = (job_row.plan or {}).get("kb_doc_id")
        result = await self.executor.execute(
            claimed,
            plan,
            decision_source,
            kb_doc_id=kb_doc_id,
            policy_version=(job_row.plan or {}).get("policy_version"),
            policy_id=(job_row.plan or {}).get("policy_id"),
        )
        return {
            "job_id": str(job_row.id),
            "status": "done",
            "destination": result.destination_path,
            "kb_doc_id": result.kb_doc_id,
            "indexing_failed": result.indexing_failed,
            "error_msg": result.error_msg,
        }

    async def _candidate_dirs(self) -> dict[str, Path]:
        await self.profiles.refresh_if_changed()
        return {p.candidate_id: p.path for p in self.profiles.profiles()}

    async def approve(self, job_id: str) -> dict:
        """批准待审计划：按存储的决策执行。"""
        job_row = await self._load_reviewable(job_id)
        decision = self._decision_from_plan(job_row.plan or {})
        return await self._execute_for_job(job_row, decision, "review")

    async def reject(self, job_id: str) -> dict:
        """兼容旧接口：语义是暂不采用推荐，文件仍留在 inbox。"""
        return await self.defer(job_id)

    async def defer(self, job_id: str) -> dict:
        """暂不归档：文件从未移动，仅记录为 unarchived。"""
        await self._load_reviewable(job_id)
        async with async_session_factory() as session:
            row = await session.get(ArchiveJob, UUID(job_id))
            assert row is not None
            row.status = "unarchived"
            row.routing_reason = "deferred"
            await session.commit()
        return {
            "job_id": job_id,
            "status": "unarchived",
            "routing_reason": "deferred",
        }

    async def replan(self, job_id: str) -> dict:
        """使用当前 policy 重新规划一个仍留在 inbox 的文件。"""
        async with async_session_factory() as session:
            row = await session.get(ArchiveJob, UUID(job_id))
            if row is None:
                raise ValueError(f"job 不存在: {job_id}")
            if row.status != "unarchived":
                raise ValueError(f"job 不在未归档状态: {row.status}")
            if not Path(row.file_path).is_file():
                raise ValueError(f"inbox 文件不存在: {row.file_path}")
            row.status = "queued"
            row.routing_reason = "replan_requested"
            row.error_msg = None
            row.locked_at = None
            row.attempts = 0
            await session.commit()
        return {"job_id": job_id, "status": "queued"}

    async def reassign(self, job_id: str, directory: str) -> dict:
        """改分类：用户指定目录（candidate_id 或 archive 内相对路径）。"""
        job_row = await self._load_reviewable(job_id)
        return await self._assign_to_directory(job_row, directory, "review")

    async def assign(self, job_id: str, directory: str) -> dict:
        """手动归档留置文件。"""
        job = await load_job(job_id)
        if job is None:
            raise ValueError(f"job 不存在: {job_id}")
        if job.status not in (
            "unarchived", "skipped", "failed", "dead", "awaiting_review"
        ):
            raise ValueError(f"job 状态不可手动归档: {job.status}")
        return await self._assign_to_directory(job, directory, "manual")

    async def _assign_to_directory(
        self, job_row: ArchiveJob, directory: str, decision_source: str
    ) -> dict:
        if not isinstance(directory, str) or not directory.strip():
            raise ValueError("directory 不能为空")
        # 人工指定：直接构造 archive 根内目录的计划（不走候选表，
        # 允许选择画像尚未覆盖的既有目录）。
        rel = directory.strip().strip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise ValueError(f"目录非法: {directory!r}")
        target = (self._archive_root / Path(*parts)).resolve()
        root = self._archive_root.resolve()
        if target == root or root not in target.parents:
            raise ValueError(f"目录越界: {directory!r}")
        if not target.is_dir():
            raise ValueError(f"目录不存在: {directory!r}")

        decision = ArchiveDecision(
            candidate_id=None,
            new_subdirectory=None,
            new_name=Path(job_row.file_path).name,
            confidence=None,
            rationale="人工指定目录",
        )
        claimed = to_claimed(job_row)
        plan = ActionPlan(
            source_path=Path(job_row.file_path),
            content_hash=job_row.content_hash,
            destination_dir=target,
            destination_path=target / decision.new_name,
            new_name=decision.new_name,
            creates_directory=False,
            decision=decision,
        )
        plan = await validate_plan_async(
            plan,
            self._archive_root,
            collision_policy=self.config.collision_policy,
        )
        kb_doc_id = (job_row.plan or {}).get("kb_doc_id")
        result = await self.executor.execute(
            claimed,
            plan,
            decision_source,
            kb_doc_id=kb_doc_id,
            policy_version=(job_row.plan or {}).get("policy_version"),
            policy_id=(job_row.plan or {}).get("policy_id"),
        )
        return {
            "job_id": str(job_row.id),
            "status": "done",
            "destination": result.destination_path,
            "kb_doc_id": result.kb_doc_id,
            "indexing_failed": result.indexing_failed,
            "error_msg": result.error_msg,
        }

    @staticmethod
    def _decision_from_plan(plan: dict) -> ArchiveDecision:
        decision = plan.get("decision") or {}
        confidence = decision.get("confidence")
        return ArchiveDecision(
            candidate_id=decision.get("candidate_id"),
            new_subdirectory=decision.get("new_subdirectory"),
            new_name=decision.get("new_name"),
            confidence=float(confidence) if confidence is not None else None,
            rationale=decision.get("rationale", ""),
        )

    # ── 撤销 / 重试入库 ─────────────────────────────────────────

    async def undo(self, operation_id: str) -> dict:
        return await self.journal.undo(operation_id)

    async def reindex(self, operation_id: str) -> dict:
        return await self.executor.reindex(operation_id)


def build_archive_runtime(
    config: ArchiveRuntimeConfig, kb: KnowledgeBase
) -> ArchiveRuntime:
    return ArchiveRuntime(config, kb)
