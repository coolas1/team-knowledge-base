"""集中式日志管理：DB 持久化 + SSE 实时广播 + trace_id 全链路追踪。

架构：
- DBLogHandler：logging.Handler 子类，每条日志异步写入 PostgreSQL 并广播给 SSE 订阅者
- LogManager：管理 SSE 订阅者队列，提供查询/清理 API
- pipeline_trace：上下文管理器，为 pipeline 执行期间的所有日志注入 trace_id
- setup_logging()：全局初始化，在 main.py lifespan 中调用
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import threading
import uuid as _uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Generator

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LogEntry
from src.db.postgres import async_session_factory

logger = logging.getLogger(__name__)

# ── trace_id 上下文变量 ─────────────────────────────────────
_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


@contextmanager
def pipeline_trace(trace_id: str | None = None) -> Generator[str, None, None]:
    """为当前 async 任务设置 trace_id 上下文。

    在此上下文内所有 logger 调用都会自动携带 trace_id。
    若未传入 trace_id，则自动生成一个。

    Usage:
        with pipeline_trace() as tid:
            logger.info("pipeline 开始")  # 自动带 trace_id
    """
    tid = trace_id or str(_uuid.uuid4())[:12]
    token = _current_trace_id.set(tid)
    try:
        yield tid
    finally:
        _current_trace_id.reset(token)


# ── 辅助函数 ─────────────────────────────────────────────────
_DOC_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _extract_doc_id(message: str) -> str | None:
    """尝试从日志消息中提取第一个 UUID 作为 doc_id。"""
    m = _DOC_ID_PATTERN.search(message)
    return m.group(0) if m else None


# ── DBLogHandler ─────────────────────────────────────────────

class DBLogHandler(logging.Handler):
    """将日志记录持久化到 PostgreSQL 并广播给 SSE 订阅者。

    写入策略：使用后台 asyncio 任务批量写入，避免阻塞日志调用方。
    trace_id 通过 contextvars 自动提取（由 pipeline_trace 上下文设置）。
    """

    def __init__(self, log_manager: LogManager) -> None:
        super().__init__()
        self._manager = log_manager
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._flush_task: asyncio.Task | None = None
        self._flush_pending = False

    def emit(self, record: logging.LogRecord) -> None:
        """logging.Handler 核心方法：缓冲日志条目，定期刷入 DB。"""
        try:
            trace_id = _current_trace_id.get(None)
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc),
                "level": record.levelname,
                "module": record.name,
                "message": self.format(record),
                "doc_id": _extract_doc_id(record.getMessage()),
                "trace_id": trace_id,
                "extra": self._build_extra(record),
            }
            with self._lock:
                self._buffer.append(entry)

            # 广播给 SSE 订阅者（非阻塞）
            sse_payload = {
                "timestamp": entry["timestamp"].isoformat(),
                "level": entry["level"],
                "module": entry["module"],
                "message": entry["message"],
                "doc_id": entry["doc_id"],
                "trace_id": trace_id,
            }
            self._manager.broadcast(sse_payload)

            # 缓冲满 5 条或 WARNING 级别以上立即刷盘
            if len(self._buffer) >= 5 or record.levelno >= logging.WARNING:
                self._schedule_flush()
            elif not self._flush_pending:
                # 低流量时延迟 2 秒刷盘，确保不会丢失
                self._flush_pending = True
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(2.0, self._schedule_flush)
                except RuntimeError:
                    pass

        except Exception:
            self.handleError(record)

    def _build_extra(self, record: logging.LogRecord) -> dict | None:
        """提取扩展信息（堆栈等）。"""
        extra: dict[str, Any] = {}
        if record.exc_info and record.exc_info[1]:
            extra["traceback"] = self.format(record)
        return extra if extra else None

    def _schedule_flush(self) -> None:
        """调度异步刷盘任务。"""
        try:
            loop = asyncio.get_running_loop()
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.ensure_future(self._flush())
        except RuntimeError:
            pass

    async def _flush(self) -> None:
        """将缓冲区日志批量写入 PostgreSQL。"""
        self._flush_pending = False
        with self._lock:
            if not self._buffer:
                return
            entries = self._buffer[:]
            self._buffer.clear()

        try:
            async with async_session_factory() as session:
                for entry in entries:
                    session.add(LogEntry(**entry))
                await session.commit()
        except Exception:
            pass

    async def close_async(self) -> None:
        """关闭前刷入剩余日志。"""
        await self._flush()
        self.close()


# ── LogManager ───────────────────────────────────────────────

class LogManager:
    """日志管理器：管理 SSE 订阅者 + 提供查询/清理接口。"""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    # ── SSE 订阅 ─────────────────────────────────────────────

    async def subscribe(self) -> asyncio.Queue:
        """注册一个新的 SSE 订阅者队列。"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._subscribers.append(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        """移除 SSE 订阅者。"""
        async with self._lock:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    def broadcast(self, payload: dict[str, Any]) -> None:
        """非阻塞广播消息到所有 SSE 订阅者。"""
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def stream_sse(self) -> AsyncGenerator[str, None]:
        """SSE 流生成器，供 FastAPI StreamingResponse 使用。"""
        queue = await self.subscribe()
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    data = json.dumps(payload, ensure_ascii=False)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            await self.unsubscribe(queue)

    # ── 查询 ─────────────────────────────────────────────────

    async def query_logs(
        self,
        page: int = 1,
        page_size: int = 100,
        level: str | None = None,
        doc_id: str | None = None,
        trace_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """分页查询日志。"""
        async with async_session_factory() as session:
            stmt = select(LogEntry)

            if level:
                stmt = stmt.where(LogEntry.level == level.upper())
            if doc_id:
                stmt = stmt.where(LogEntry.doc_id == doc_id)
            if trace_id:
                stmt = stmt.where(LogEntry.trace_id == trace_id)
            if start_time:
                stmt = stmt.where(LogEntry.timestamp >= start_time)
            if end_time:
                stmt = stmt.where(LogEntry.timestamp <= end_time)

            # 总数
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0

            # 分页查询（最新在前）
            stmt = (
                stmt.order_by(LogEntry.timestamp.desc(), LogEntry.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            result = await session.execute(stmt)
            entries = result.scalars().all()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [
                    {
                        "id": e.id,
                        "timestamp": e.timestamp.isoformat(),
                        "level": e.level,
                        "module": e.module,
                        "message": e.message,
                        "doc_id": e.doc_id,
                        "trace_id": e.trace_id,
                        "extra": e.extra,
                    }
                    for e in entries
                ],
            }

    # ── 清理 ─────────────────────────────────────────────────

    async def cleanup(self, keep_days: int = 7) -> int:
        """删除超过 keep_days 天的日志，返回删除条数。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        async with async_session_factory() as session:
            count_stmt = select(func.count()).where(LogEntry.timestamp < cutoff)
            count = (await session.execute(count_stmt)).scalar() or 0
            if count > 0:
                await session.execute(delete(LogEntry).where(LogEntry.timestamp < cutoff))
                await session.commit()
            return count


# ── 全局单例 ────────────────────────────────────────────────

log_manager = LogManager()
_db_handler: DBLogHandler | None = None


def setup_logging(level: int = logging.INFO) -> None:
    """初始化全局日志系统：注册 DBLogHandler 到 root logger。

    应在 main.py lifespan startup 中调用。
    """
    global _db_handler

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台输出（开发调试用）
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console)

    # DB 持久化 + SSE 广播
    _db_handler = DBLogHandler(log_manager)
    _db_handler.setLevel(level)
    _db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(_db_handler)


async def shutdown_logging() -> None:
    """关闭时刷入剩余日志。应在 lifespan shutdown 中调用。"""
    if _db_handler:
        await _db_handler.close_async()
