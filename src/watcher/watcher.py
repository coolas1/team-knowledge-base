"""文件系统变更监听器：感知层，检测文件变更并更新版本记录。"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from src.db.models import Document, DocumentVersion
from src.db.postgres import async_session_factory
from src.pipeline.extractors.registry import ExtractorRegistry
from src.watcher.config import WatchConfig

logger = logging.getLogger(__name__)


def _compute_file_hash(file_path: Path) -> str:
    """计算文件的 SHA256。"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_excluded(relative_path: str, patterns: list[str]) -> bool:
    """检查文件是否匹配排除规则。"""
    # 始终排除版本快照目录
    parts = Path(relative_path).parts
    if "versions" in parts:
        return True
    for pattern in patterns:
        if fnmatch.fnmatch(relative_path, pattern):
            return True
        # 也检查文件名本身
        if fnmatch.fnmatch(Path(relative_path).name, pattern):
            return True
    return False


def _get_supported_extensions() -> set[str]:
    """获取系统支持的文件扩展名集合。"""
    return {
        ".md", ".markdown", ".txt",
        ".pdf",
        ".docx",
        ".pptx",
        ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp",
    }


class _WatchdogHandler(FileSystemEventHandler):
    """watchdog 事件处理器，在后台线程中运行。"""

    def __init__(
        self,
        watcher: FileWatcher,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._watcher = watcher
        self._loop = loop

    def _schedule(self, coro) -> None:
        """将协程调度到主 asyncio 事件循环。"""
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self._schedule(self._watcher._on_file_changed(Path(event.src_path), "create"))

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self._schedule(self._watcher._on_file_changed(Path(event.src_path), "modify"))

    def on_deleted(self, event: FileDeletedEvent) -> None:
        if not event.is_directory:
            self._schedule(self._watcher._on_file_deleted(Path(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self._schedule(
                self._watcher._on_file_moved(Path(event.src_path), Path(event.dest_path))
            )


class FileWatcher:
    """文件系统变更监听器。"""

    # 防抖窗口：同一文件在此时间内的重复事件会被忽略
    _DEBOUNCE_SECONDS = 2.0

    def __init__(self, config: WatchConfig) -> None:
        self._config = config
        self._observer: Observer | None = None
        self._supported_exts = _get_supported_extensions()
        self._dir_paths: list[Path] = [Path(d.path) for d in config.directories]
        # 防抖：记录每个文件路径最近一次处理的时间戳
        self._last_event_time: dict[str, float] = {}
        self._event_lock = threading.Lock()

    async def start(self) -> None:
        """启动：执行全量扫描 + 启动 watchdog 后台线程。"""
        if not self._config.enabled or not self._config.directories:
            logger.info("目录监控未启用或未配置目录，跳过启动")
            return

        # 全量扫描
        await self._full_scan()

        # 启动 watchdog
        self._start_watchdog()
        logger.info(
            f"目录监控已启动: {len(self._config.directories)} 个目录, "
            f"{len(self._config.exclude_patterns)} 条排除规则"
        )

    async def stop(self) -> None:
        """停止 watchdog 后台线程。"""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("目录监控已停止")

    # ── 全量扫描 ──────────────────────────────────────────────

    async def _full_scan(self) -> None:
        """启动时全量扫描：对比目录文件与 DB 记录，标记差异。"""
        t0 = time.monotonic()
        trace_id = uuid.uuid4().hex[:8]
        logger.info(f"[trace={trace_id}] 开始全量扫描...")
        scanned_files: dict[str, tuple[Path, str]] = {}  # relative_path -> (abs_path, hash)

        # 1. 遍历所有配置目录，收集文件
        for dir_config in self._config.directories:
            dir_path = Path(dir_config.path)
            if not dir_path.exists():
                logger.warning(f"监控目录不存在: {dir_path}")
                continue

            pattern = "**/*" if dir_config.recursive else "*"
            for file_path in dir_path.glob(pattern):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in self._supported_exts:
                    continue
                rel_path = str(file_path.relative_to(dir_path))
                if _is_excluded(rel_path, self._config.exclude_patterns):
                    continue
                try:
                    ht = time.monotonic()
                    file_hash = _compute_file_hash(file_path)
                    hash_ms = (time.monotonic() - ht) * 1000
                    if hash_ms > 100:  # 只记录慢文件
                        logger.info(f"[trace={trace_id}] hash 计算慢: {file_path.name} | {hash_ms:.0f}ms")
                except Exception as e:
                    logger.warning(f"[trace={trace_id}] 无法计算文件 hash: {file_path} - {e}")
                    continue
                scanned_files[rel_path] = (file_path, file_hash)

        # 2. 与 DB 对比
        async with async_session_factory() as session:
            # 获取所有 watch 类型文档
            from sqlalchemy import select
            result = await session.execute(
                select(Document).where(Document.source_type == "watch")
            )
            existing_docs: dict[str, Document] = {
                d.source_path: d for d in result.scalars().all() if d.source_path
            }

            created = 0
            updated = 0
            disappeared = 0

            # 检查扫描到的文件
            for rel_path, (abs_path, file_hash) in scanned_files.items():
                doc = existing_docs.get(rel_path)
                if doc is None:
                    # 新文件
                    await self._create_document_record(
                        session, abs_path, rel_path, file_hash, str(abs_path.parent)
                    )
                    created += 1
                elif doc.content_hash != file_hash:
                    # 内容变更
                    await self._create_version_for_doc(
                        session, doc, abs_path, file_hash, "modify"
                    )
                    doc.index_status = "stale"
                    updated += 1
                # 否则内容相同，跳过

            # 检查 DB 中有但目录中消失的文件
            for rel_path, doc in existing_docs.items():
                if rel_path not in scanned_files and doc.file_status == "active":
                    doc.file_status = "disappeared"
                    disappeared += 1

            # 清理误入库的版本快照文件（历史 bug：versions/ 目录下的文件被当作新文档）
            from sqlalchemy import or_
            from sqlalchemy import delete as sql_delete
            versions_cleanup = await session.execute(
                select(Document).where(
                    Document.source_type == "watch",
                    or_(
                        Document.source_path.like("versions/%"),   # type: ignore[union-attr]
                        Document.source_path.like("versions\\%"),  # type: ignore[union-attr]
                    ),
                )
            )
            stale_version_docs = versions_cleanup.scalars().all()
            if stale_version_docs:
                for vd in stale_version_docs:
                    await session.execute(
                        sql_delete(DocumentVersion).where(DocumentVersion.doc_id == vd.id)
                    )
                await session.execute(
                    sql_delete(Document).where(
                        Document.id.in_([vd.id for vd in stale_version_docs])  # type: ignore[union-attr]
                    )
                )
                logger.info(
                    f"[trace={trace_id}] 清理误入库版本快照文档: {len(stale_version_docs)} 个"
                )

            await session.commit()

        scan_ms = (time.monotonic() - t0) * 1000
        logger.info(
            f"[trace={trace_id}] 全量扫描完成: 扫描 {len(scanned_files)} 个文件 | "
            f"新建 {created} | 更新 {updated} | 消失 {disappeared} | 耗时 {scan_ms:.0f}ms"
        )

    # ── watchdog 启动 ─────────────────────────────────────────

    def _start_watchdog(self) -> None:
        """启动 watchdog Observer 后台线程。"""
        loop = asyncio.get_running_loop()
        handler = _WatchdogHandler(self, loop)
        self._observer = Observer()

        for dir_config in self._config.directories:
            dir_path = Path(dir_config.path)
            if dir_path.exists():
                self._observer.schedule(handler, str(dir_path), recursive=dir_config.recursive)
                logger.info(f"已监听目录: {dir_path} (recursive={dir_config.recursive})")

        self._observer.start()

    # ── 事件处理 ──────────────────────────────────────────────

    async def _on_file_changed(self, file_path: Path, event_type: str) -> None:
        """文件创建/修改回调（内置防抖，同一文件 2s 内的重复事件只处理一次）。"""
        # ── 防抖：同一文件在窗口内的重复事件直接跳过 ──
        path_key = str(file_path)
        now = time.monotonic()
        with self._event_lock:
            last = self._last_event_time.get(path_key, 0)
            if now - last < self._DEBOUNCE_SECONDS:
                return  # 重复事件，跳过
            self._last_event_time[path_key] = now

        trace_id = uuid.uuid4().hex[:8]
        t0 = time.monotonic()

        # 检查扩展名
        if file_path.suffix.lower() not in self._supported_exts:
            return

        # 检查排除规则
        rel_path = self._get_relative_path(file_path)
        if rel_path and _is_excluded(rel_path, self._config.exclude_patterns):
            return

        try:
            ht = time.monotonic()
            file_hash = _compute_file_hash(file_path)
            hash_ms = (time.monotonic() - ht) * 1000
            logger.info(
                f"[trace={trace_id}] [watchdog] {event_type} 事件: {file_path.name} | "
                f"hash计算 {hash_ms:.0f}ms"
            )
        except Exception as e:
            logger.warning(f"[trace={trace_id}] 无法计算文件 hash: {file_path} - {e}")
            return

        async with async_session_factory() as session:
            from sqlalchemy import select
            # 查找已有文档
            if rel_path:
                result = await session.execute(
                    select(Document).where(
                        Document.source_path == rel_path,
                        Document.source_type == "watch",
                    )
                )
                doc = result.scalar_one_or_none()
            else:
                doc = None

            if doc is None:
                # 新文件
                watch_dir = self._find_watch_dir(file_path)
                await self._create_document_record(
                    session, file_path, rel_path or file_path.name,
                    file_hash, str(watch_dir) if watch_dir else ""
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    f"[trace={trace_id}] [watchdog] 新文件入库: {file_path.name} | "
                    f"hash={file_hash[:12]} | 耗时 {elapsed_ms:.0f}ms"
                )
            elif doc.content_hash != file_hash:
                # 内容变更
                await self._create_version_for_doc(
                    session, doc, file_path, file_hash, "modify"
                )
                doc.index_status = "stale"
                elapsed_ms = (time.monotonic() - t0) * 1000
                logger.info(
                    f"[trace={trace_id}] [watchdog] 文件变更: {file_path.name} → stale | "
                    f"hash {doc.content_hash[:12]}→{file_hash[:12]} | 耗时 {elapsed_ms:.0f}ms"
                )
            # else: hash 相同，幂等跳过

            await session.commit()

    async def _on_file_deleted(self, file_path: Path) -> None:
        """文件删除回调。"""
        trace_id = uuid.uuid4().hex[:8]
        rel_path = self._get_relative_path(file_path)
        if not rel_path:
            return

        async with async_session_factory() as session:
            from sqlalchemy import select, update
            result = await session.execute(
                select(Document).where(
                    Document.source_path == rel_path,
                    Document.source_type == "watch",
                )
            )
            doc = result.scalar_one_or_none()
            if doc and doc.file_status == "active":
                doc.file_status = "disappeared"
                await session.commit()
                logger.info(f"[trace={trace_id}] [watchdog] 文件消失: {file_path.name} | doc_id={doc.id}")

    async def _on_file_moved(self, src_path: Path, dest_path: Path) -> None:
        """文件移动/重命名回调（快速路径）。"""
        trace_id = uuid.uuid4().hex[:8]
        # 检查目标文件扩展名
        if dest_path.suffix.lower() not in self._supported_exts:
            return

        src_rel = self._get_relative_path(src_path)
        dest_rel = self._get_relative_path(dest_path)
        if not src_rel or not dest_rel:
            return

        if _is_excluded(dest_rel, self._config.exclude_patterns):
            return

        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Document).where(
                    Document.source_path == src_rel,
                    Document.source_type == "watch",
                )
            )
            doc = result.scalar_one_or_none()
            if doc:
                # 检查内容是否也变了
                try:
                    new_hash = _compute_file_hash(dest_path)
                except Exception:
                    return

                if doc.content_hash == new_hash:
                    # 仅重命名，内容不变 → 只更新元数据
                    doc.source_path = dest_rel
                    doc.title = dest_path.name
                    doc.file_path = str(dest_path)
                    await session.commit()
                    logger.info(
                        f"[trace={trace_id}] [watchdog] 文件重命名: "
                        f"{src_path.name} → {dest_path.name} | doc_id={doc.id}"
                    )
                else:
                    # 重命名+改内容 → 当作新文档
                    doc.file_status = "disappeared"
                    await self._create_document_record(
                        session, dest_path, dest_rel, new_hash,
                        str(self._find_watch_dir(dest_path) or "")
                    )
                    await session.commit()
                    logger.info(
                        f"[trace={trace_id}] [watchdog] 重命名+改内容: "
                        f"{src_path.name} → {dest_path.name} (新文档) | doc_id={doc.id}"
                    )

    # ── 辅助方法 ──────────────────────────────────────────────

    def _get_relative_path(self, file_path: Path) -> str | None:
        """获取文件相对于最近监控目录的相对路径。"""
        for dir_path in self._dir_paths:
            try:
                return str(file_path.relative_to(dir_path))
            except ValueError:
                continue
        return None

    def _find_watch_dir(self, file_path: Path) -> Path | None:
        """找到文件所属的监控目录。"""
        for dir_path in self._dir_paths:
            try:
                file_path.relative_to(dir_path)
                return dir_path
            except ValueError:
                continue
        return None

    async def _create_document_record(
        self,
        session,
        file_path: Path,
        rel_path: str,
        file_hash: str,
        watch_dir: str,
    ) -> Document:
        """创建新的 Document + Version 记录。"""
        import uuid
        from src.pipeline.extractors.registry import ExtractorRegistry

        doc_id = uuid.uuid4()
        file_type = ExtractorRegistry.guess_file_type(file_path)

        doc = Document(
            id=doc_id,
            title=file_path.name,
            file_type=file_type,
            file_path=str(file_path),
            source_type="watch",
            source_path=rel_path,
            watch_dir=watch_dir,
            index_status="pending",
            file_status="active",
            content_hash=file_hash,
        )
        session.add(doc)

        # 创建 version=1
        version = DocumentVersion(
            doc_id=doc_id,
            version=1,
            raw_text="",  # 全量扫描时不提取文本，等 Pipeline 执行
            content_hash=file_hash,
            file_path=str(file_path),
            change_type="create",
        )
        session.add(version)
        logger.info(
            f"创建文档记录: {file_path.name} | doc_id={doc_id} | "
            f"type={file_type} | hash={file_hash[:12]}"
        )
        return doc

    async def _create_version_for_doc(
        self,
        session,
        doc: Document,
        file_path: Path,
        file_hash: str,
        change_type: str,
    ) -> DocumentVersion:
        """为已有文档创建新版本记录。"""
        from sqlalchemy import select, func

        # 获取当前最大版本号
        result = await session.execute(
            select(func.max(DocumentVersion.version)).where(DocumentVersion.doc_id == doc.id)
        )
        max_version = result.scalar() or 0

        new_version = max_version + 1
        version = DocumentVersion(
            doc_id=doc.id,
            version=new_version,
            raw_text="",  # Pipeline 执行时填充
            content_hash=file_hash,
            file_path=str(file_path),
            change_type=change_type,
        )
        session.add(version)
        doc.content_hash = file_hash
        logger.info(
            f"创建版本记录: doc_id={doc.id} | v{new_version} | "
            f"change={change_type} | hash={file_hash[:12]}"
        )
        return version
