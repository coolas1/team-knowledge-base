"""Pipeline 执行调度器：定时任务 + 手动触发。"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.core.knowledge_base import KnowledgeBase
from src.db.models import Document
from src.db.postgres import async_session_factory
from src.watcher.config import WatchConfig

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineScheduler:
    """Pipeline 执行调度器。"""

    def __init__(self, kb: KnowledgeBase, config: WatchConfig) -> None:
        self._kb = kb
        self._config = config
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_pipeline_at: datetime | None = None
        # Pipeline 互斥锁：同一文档不并发执行
        self._processing_docs: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动定时任务循环。"""
        if not self._config.pipeline.enabled:
            logger.info("Pipeline 定时同步已禁用")
            return
        if self._config.pipeline.schedule_hours <= 0:
            logger.info("Pipeline 定时同步间隔为 0，不启动定时任务")
            return

        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        logger.info(
            f"Pipeline 定时同步已启动: 每 {self._config.pipeline.schedule_hours} 小时执行一次"
        )

    async def stop(self) -> None:
        """停止定时任务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Pipeline 定时同步已停止")

    async def trigger_manual(self) -> dict[str, Any]:
        """手动触发：立即处理所有 pending/stale 文档。"""
        logger.info("手动触发 Pipeline 同步")
        count = await self._process_pending_documents()
        self._last_pipeline_at = _utcnow()
        return {
            "triggered": True,
            "pending_count": count,
            "message": f"已触发 {count} 个文档的索引任务",
        }

    async def get_status(self) -> dict[str, Any]:
        """获取当前同步状态。"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Document).where(
                    Document.index_status.in_(["pending", "stale"])  # type: ignore[union-attr]
                )
            )
            pending_count = len(result.scalars().all())

        schedule_hours = self._config.pipeline.schedule_hours
        next_at = None
        if self._last_pipeline_at and schedule_hours > 0:
            from datetime import timedelta
            next_at = self._last_pipeline_at + timedelta(hours=schedule_hours)

        return {
            "watch_enabled": self._config.enabled,
            "watch_directories": [d.path for d in self._config.directories],
            "last_pipeline_at": self._last_pipeline_at.isoformat() if self._last_pipeline_at else None,
            "pending_count": pending_count,
            "schedule_hours": schedule_hours,
            "next_scheduled_at": next_at.isoformat() if next_at else None,
        }

    # ── 内部方法 ──────────────────────────────────────────────

    async def _schedule_loop(self) -> None:
        """定时循环：每隔 N 小时执行一次。"""
        interval_seconds = self._config.pipeline.schedule_hours * 3600
        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                if not self._running:
                    break
                logger.info("定时同步开始...")
                count = await self._process_pending_documents()
                self._last_pipeline_at = _utcnow()
                logger.info(f"定时同步完成: 处理了 {count} 个文档")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时同步异常: {e}", exc_info=True)

    async def _process_pending_documents(self) -> int:
        """处理所有 pending/stale 文档。"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(Document).where(
                    Document.index_status.in_(["pending", "stale"]),  # type: ignore[union-attr]
                    Document.file_status == "active",
                )
            )
            docs = result.scalars().all()

        if not docs:
            logger.info("无待处理文档")
            return 0

        logger.info(f"发现 {len(docs)} 个待处理文档")
        processed = 0
        t_total = time.monotonic()

        for doc in docs:
            doc_id_str = str(doc.id)

            # 互斥锁检查
            async with self._lock:
                if doc_id_str in self._processing_docs:
                    logger.info(f"文档 {doc_id_str} 正在处理中，跳过")
                    continue
                self._processing_docs.add(doc_id_str)

            try:
                from pathlib import Path
                file_path = Path(doc.file_path) if doc.file_path else None

                t_doc = time.monotonic()
                logger.info(
                    f"Pipeline 开始处理: doc_id={doc_id_str} | "
                    f"title={doc.title} | index_status={doc.index_status}"
                )

                if file_path and file_path.exists():
                    # 有原始文件 → 走完整 Pipeline
                    await self._kb._pipeline.process_file(
                        doc.id, file_path, doc.title, doc.file_type
                    )
                elif doc.raw_text:
                    # 无原始文件但有 raw_text → 走 re-index
                    await self._kb._pipeline.reindex_document(doc.id, doc.raw_text)
                else:
                    logger.warning(f"文档 {doc_id_str} 无原始文件也无 raw_text，跳过")
                    continue

                doc_ms = (time.monotonic() - t_doc) * 1000
                logger.info(
                    f"Pipeline 处理完成: doc_id={doc_id_str} | "
                    f"title={doc.title} | 耗时 {doc_ms:.0f}ms"
                )
                processed += 1
            except Exception as e:
                logger.error(f"文档 {doc_id_str} Pipeline 执行失败: {e}", exc_info=True)
            finally:
                async with self._lock:
                    self._processing_docs.discard(doc_id_str)

        total_ms = (time.monotonic() - t_total) * 1000
        logger.info(
            f"批量处理完成: {processed}/{len(docs)} 成功 | 总耗时 {total_ms:.0f}ms"
        )
        return processed
