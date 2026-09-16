"""归档 worker：单个 job 的处理编排。

claim -> 提取 FileContext ->（归档分类 || GraphRAG）-> 建计划 -> 校验 ->
高置信自动执行 / 低置信待确认。自动路径与人审批准共用同一条执行代码。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.engine.components.extractors.registry import registry
from src.engine.components.store.models import ArchiveJob
from src.engine.components.store.postgres import async_session_factory

from .classifier import ArchiveClassifier, ClassificationError, ArchiveDecision
from .config import ArchiveRuntimeConfig
from .executor import ArchiveExecutor
from .jobs import ArchiveJobQueue, ClaimedJob
from .planner import PlanError, build_plan, validate_plan_async
from .policy import ArchivePolicyStore

logger = logging.getLogger(__name__)

Route = Literal["auto", "review"]

# 进入分类 prompt 的文件摘要长度。
_SUMMARY_CHARS = 3000


def route_decision(
    decision: ArchiveDecision,
    *,
    threshold: float,
    delta: float = 0.0,
    review_all: bool,
    allow_new_directories: bool = True,
) -> tuple[Route, str]:
    """二态置信度分流（纯函数）。

    - review_all：全部进待审（测试模式）
    - >= threshold：自动执行（可按策略新建目录）
    - < threshold：给出推荐，文件留在 inbox 等用户确认

    delta 暂留在签名中兼容旧配置，V2 不再使用置信度带。
    """
    del delta
    if review_all:
        return "review", "review_all_mode"
    if decision.new_subdirectory is not None and not allow_new_directories:
        return "review", "new_directory_requires_confirmation"
    if decision.confidence >= threshold:
        return "auto", "high_confidence"
    return "review", "low_confidence"


@dataclass(slots=True)
class WorkerOutcome:
    job_id: str
    file_name: str
    status: str
    routing_reason: str | None = None
    destination: str | None = None
    error: str | None = None


class ArchiveWorker:
    def __init__(
        self,
        queue: ArchiveJobQueue,
        classifier: ArchiveClassifier,
        executor: ArchiveExecutor,
        config: ArchiveRuntimeConfig,
        policy_store: ArchivePolicyStore | None = None,
    ) -> None:
        self._queue = queue
        self._classifier = classifier
        self._executor = executor
        self._config = config
        self._policy_store = policy_store
        # 运行时可切换的审核模式（PUT /api/archive/mode）。
        self.review_all = config.review_all

    async def run_once(self) -> WorkerOutcome | None:
        job = await self._queue.claim(
            lease_seconds=300, max_attempts=self._config.max_attempts
        )
        if job is None:
            return None
        try:
            return await self._process(job)
        except ClassificationError as exc:
            return await self._fail(job, str(exc), retryable=True)
        except PlanError as exc:
            # 校验失败是确定性的，重试无意义。
            return await self._fail(job, str(exc), retryable=False)
        except Exception as exc:
            logger.exception("归档 job %s 处理失败", job.id)
            return await self._fail(job, str(exc), retryable=True)

    async def _process(self, job: ClaimedJob) -> WorkerOutcome:
        source = Path(job.file_path)

        # 1. FileContext：复用提取层（与知识库入库共享同一次提取语义）
        try:
            text = await asyncio.to_thread(registry.extract, source)
        except Exception as exc:
            return await self._fail(job, f"文本提取失败: {exc}", retryable=False)
        summary = text[:_SUMMARY_CHARS] if text else f"（无可提取文本，文件名: {job.file_name}）"

        policy_version: int | None = None
        policy_id: str | None = None
        allow_new_directories = True
        max_directory_depth = 2
        if self._policy_store is not None:
            policy = await self._policy_store.current()
            policy_version = policy.version
            policy_id = str(policy.id)
            allow_new_directories = bool(
                policy.rules.get("allow_new_directories", True)
            )
            max_directory_depth = int(
                policy.rules.get("max_directory_depth", 2)
            )
            self._classifier.policy_instructions = (
                str(policy.rules.get("instructions", "")) if policy.enabled else ""
            )
            self._classifier.max_directory_depth = max_directory_depth

        # 2. 两条流水线共享 FileContext 后并行启动：
        #    A) 目录候选检索 + LLM 整文件分类
        #    B) 原有知识库 chunk/overview/embedding/图谱处理
        classify_task = asyncio.create_task(
            self._classifier.classify(
                job.file_name, summary, top_k=self._config.top_k
            )
        )
        knowledge_task = asyncio.create_task(
            self._executor.prepare_knowledge(job, text)
        )
        classification, knowledge = await asyncio.gather(
            classify_task, knowledge_task, return_exceptions=True
        )
        if isinstance(classification, BaseException):
            raise classification
        decision, candidates = classification
        kb_doc_id: str | None = None
        knowledge_error: str | None = None
        if isinstance(knowledge, BaseException):
            knowledge_error = str(knowledge)
            logger.error(
                "文件 %s 的知识库预处理启动失败",
                job.file_name,
                exc_info=(type(knowledge), knowledge, knowledge.__traceback__),
            )
        else:
            kb_doc_id = knowledge

        # 3. 计划 + 校验
        candidate_dirs = {p.candidate_id: p.path for p in candidates}
        plan = build_plan(
            job,
            decision,
            Path(self._config.archive_root),
            candidate_dirs=candidate_dirs,
            max_directory_depth=max_directory_depth,
        )
        plan = await validate_plan_async(
            plan, Path(self._config.archive_root), collision_policy=self._config.collision_policy
        )

        plan_payload = {
            "decision": decision.to_dict(),
            "candidates": [
                {
                    "candidate_id": p.candidate_id,
                    "description": p.description,
                    "doc_count": p.doc_count,
                }
                for p in candidates
            ],
            "destination": str(plan.destination_path),
            "validation_notes": plan.validation_notes,
            "kb_doc_id": kb_doc_id,
            "knowledge_error": knowledge_error,
            "policy_version": policy_version,
            "policy_id": policy_id,
            "max_directory_depth": max_directory_depth,
        }

        # 4. 二态分流
        route, reason = route_decision(
            decision,
            threshold=self._config.threshold,
            delta=self._config.delta,
            review_all=self.review_all,
            allow_new_directories=allow_new_directories,
        )
        if route == "review":
            await self._queue.set_status(
                job.id, "awaiting_review", plan=plan_payload, routing_reason=reason
            )
            return WorkerOutcome(
                job_id=job.id, file_name=job.file_name,
                status="awaiting_review", routing_reason=reason,
                destination=str(plan.destination_path),
            )
        # 5. 自动执行
        result = await self._executor.execute(
            job,
            plan,
            "auto",
            kb_doc_id=kb_doc_id,
            extracted_text=text if kb_doc_id is None else None,
            policy_version=policy_version,
            policy_id=policy_id,
        )
        return WorkerOutcome(
            job_id=job.id, file_name=job.file_name,
            status="done" if not result.indexing_failed else "done_indexing_failed",
            destination=result.destination_path,
            error=result.error_msg,
        )

    async def _fail(
        self, job: ClaimedJob, error: str, *, retryable: bool
    ) -> WorkerOutcome:
        if not retryable or job.attempts >= self._config.max_attempts:
            await self._queue.mark_dead(job.id, job.attempts, error)
            return WorkerOutcome(
                job_id=job.id, file_name=job.file_name, status="dead", error=error
            )
        delay = min(2 ** min(job.attempts, 6), 60)
        await self._queue.fail(job.id, job.attempts, error, retry_delay_seconds=delay)
        return WorkerOutcome(
            job_id=job.id, file_name=job.file_name, status="failed", error=error
        )


async def load_job(job_id: str) -> ArchiveJob | None:
    from uuid import UUID

    async with async_session_factory() as session:
        return await session.get(ArchiveJob, UUID(job_id))
