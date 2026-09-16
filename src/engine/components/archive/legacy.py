"""Sortio 式存量归档：扫描 -> 生成可预览方案 -> 用户选择后执行。"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from src.engine.components.store.models import (
    ArchiveMigrationBatch,
    ArchiveOperation,
    Document,
    INTERNAL_DOCUMENT_FILE_TYPES,
)
from src.engine.components.store.postgres import async_session_factory

from .classifier import ArchiveClassifier, ClassificationError
from .hashing import file_sha256_async
from .planner import _resolve_new_subdirectory, safe_component
from .policy import ArchivePolicyStore


def _available_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


class LegacyArchiveService:
    def __init__(
        self,
        archive_root: Path,
        classifier: ArchiveClassifier,
        policies: ArchivePolicyStore,
        *,
        threshold: float,
        top_k: int,
    ) -> None:
        self._root = archive_root.resolve()
        self._classifier = classifier
        self._policies = policies
        self._threshold = threshold
        self._top_k = top_k

    async def scan(self) -> list[dict]:
        """列出知识库中尚未位于 archive/ 的当前版本文档，不做移动。"""
        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(Document)
                    .where(
                        Document.is_current.is_(True),
                        Document.file_path.is_not(None),
                        Document.file_type.not_in(INTERNAL_DOCUMENT_FILE_TYPES),
                    )
                    .order_by(Document.created_at)
                )
            ).scalars().all()
        items = []
        for doc in rows:
            source = Path(doc.file_path)
            try:
                already_archived = source.resolve().is_relative_to(self._root)
            except (OSError, RuntimeError):
                already_archived = False
            if already_archived:
                continue
            items.append(
                {
                    "doc_id": str(doc.id),
                    "title": doc.title,
                    "file_path": doc.file_path,
                    "file_exists": source.is_file(),
                    "status": doc.status,
                }
            )
        return items

    async def plan(self, document_ids: list[str] | None = None) -> dict:
        """按当前策略生成 dry-run 方案，文件与 documents 均不改变。"""
        policy = await self._policies.current()
        self._classifier.policy_instructions = (
            str(policy.rules.get("instructions", "")) if policy.enabled else ""
        )
        max_directory_depth = int(policy.rules.get("max_directory_depth", 2))
        self._classifier.max_directory_depth = max_directory_depth
        wanted = {UUID(item) for item in document_ids} if document_ids else None
        async with async_session_factory() as session:
            statement = select(Document).where(
                Document.is_current.is_(True),
                Document.file_path.is_not(None),
                Document.file_type.not_in(INTERNAL_DOCUMENT_FILE_TYPES),
            )
            if wanted is not None:
                statement = statement.where(Document.id.in_(wanted))
            docs = (await session.execute(statement)).scalars().all()

        plan_items: list[dict] = []
        for doc in docs:
            source = Path(doc.file_path)
            try:
                if source.resolve().is_relative_to(self._root):
                    continue
            except (OSError, RuntimeError):
                pass
            if not source.is_file():
                plan_items.append(
                    {
                        "doc_id": str(doc.id),
                        "title": doc.title,
                        "source_path": str(source),
                        "error": "原始文件不存在",
                    }
                )
                continue
            summary = (doc.raw_text or doc.overview or doc.title)[:3000]
            try:
                decision, _candidates = await self._classifier.classify(
                    doc.title, summary, top_k=self._top_k
                )
            except ClassificationError:
                # 模型不可用时仍返回可展示的 dry-run 条目，但不给出伪分类，
                # 也不允许执行，避免“待整理”目录污染后续目录画像。
                plan_items.append(
                    {
                        "doc_id": str(doc.id),
                        "title": doc.title,
                        "source_path": str(source),
                        "error": "分类模型暂时不可用，尚未生成分类建议，请稍后重试",
                        "retryable": True,
                    }
                )
                continue
            rel_dir = decision.candidate_id or decision.new_subdirectory
            assert rel_dir is not None
            if decision.new_subdirectory is not None:
                _resolve_new_subdirectory(
                    decision.new_subdirectory,
                    max_depth=max_directory_depth,
                )
            destination = self._root / rel_dir / (decision.new_name or source.name)
            plan_items.append(
                {
                    "doc_id": str(doc.id),
                    "title": doc.title,
                    "source_path": str(source),
                    "destination_path": str(destination),
                    "directory": rel_dir,
                    "new_name": decision.new_name or source.name,
                    "confidence": decision.confidence,
                    "rationale": decision.rationale,
                    "creates_directory": not destination.parent.exists(),
                    "requires_confirmation": decision.confidence < self._threshold,
                    "max_directory_depth": max_directory_depth,
                }
            )

        async with async_session_factory() as session:
            batch = ArchiveMigrationBatch(
                policy_version=policy.version,
                policy_id=policy.id,
                status="planned",
                plan=plan_items,
                result={},
            )
            session.add(batch)
            await session.commit()
            await session.refresh(batch)
        return {
            "batch_id": str(batch.id),
            "policy_version": policy.version,
            "items": plan_items,
        }

    async def execute(
        self,
        batch_id: str,
        document_ids: list[str] | None = None,
        overrides: dict[str, dict] | None = None,
    ) -> dict:
        """只执行用户选中的 dry-run 项；空 document_ids 表示执行全部可执行项。"""
        selected = set(document_ids or [])
        overrides = overrides or {}
        async with async_session_factory() as session:
            batch = await session.get(ArchiveMigrationBatch, UUID(batch_id))
            if batch is None:
                raise ValueError(f"迁移批次不存在: {batch_id}")
            if batch.status not in ("planned", "partial"):
                raise ValueError(f"迁移批次不可执行: {batch.status}")
            plan_items = list(batch.plan or [])

        results: list[dict] = []
        for item in plan_items:
            doc_id = item.get("doc_id")
            if item.get("error") or (selected and doc_id not in selected):
                continue
            source = Path(item["source_path"])
            destination = Path(item["destination_path"])
            try:
                override = overrides.get(str(doc_id), {})
                directory = override.get("directory")
                new_name = override.get("new_name")
                if directory:
                    rel = _resolve_new_subdirectory(
                        str(directory),
                        max_depth=int(item.get("max_directory_depth", 2)),
                    )
                    destination = self._root / Path(*rel.parts) / (
                        safe_component(str(new_name))
                        if new_name
                        else destination.name
                    )
                elif new_name:
                    destination = destination.with_name(safe_component(str(new_name)))
                resolved_parent = destination.parent.resolve()
                if not resolved_parent.is_relative_to(self._root):
                    raise ValueError("目标目录越界")
                if not source.is_file():
                    raise ValueError("原始文件不存在")
                resolved_parent.mkdir(parents=True, exist_ok=True)
                destination = _available_destination(resolved_parent, destination.name)
                digest = await file_sha256_async(source)
                await asyncio.to_thread(shutil.move, str(source), str(destination))
                async with async_session_factory() as session:
                    doc = await session.get(Document, UUID(doc_id))
                    if doc is None:
                        raise ValueError("知识库文档不存在")
                    doc.file_path = str(destination)
                    doc.title = destination.name
                    operation = ArchiveOperation(
                        job_id=None,
                        source_path=str(source),
                        destination_path=str(destination),
                        content_hash=digest,
                        decision_source="migration",
                        confidence=item.get("confidence"),
                        rationale=item.get("rationale"),
                        policy_id=batch.policy_id,
                        policy_version=batch.policy_version,
                        kb_doc_id=doc.id,
                        status="done",
                    )
                    session.add(operation)
                    await session.commit()
                results.append(
                    {"doc_id": doc_id, "status": "done", "destination": str(destination)}
                )
            except Exception as exc:
                results.append({"doc_id": doc_id, "status": "failed", "error": str(exc)})

        failed = sum(item["status"] == "failed" for item in results)
        status = "done" if not failed else "partial"
        summary = {"total": len(results), "done": len(results) - failed, "failed": failed, "items": results}
        async with async_session_factory() as session:
            batch = await session.get(ArchiveMigrationBatch, UUID(batch_id))
            assert batch is not None
            batch.status = status
            batch.result = summary
            await session.commit()
        return {"batch_id": batch_id, "status": status, **summary}
