"""GraphRAG backend: implements the KnowledgeBase contract.

Migrated from src/core/knowledge_base.py + src/core/search.py. The backend
owns its own DB sessions (async_session_factory); callers never pass a session.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import shutil
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload  # noqa: F401  (kept for parity with original)

from config.settings import settings
from src.engine.components.analyzer import Analyzer
from src.engine.components.embedder import embedder
from src.engine.components.extractors.registry import ExtractorRegistry, registry
from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentChange,
    is_public_document,
    public_document_filter,
)
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.components.store.postgres import async_session_factory
from src.engine.config import EngineConfig
from src.engine.graphrag.pipeline import Pipeline, VersionParent, format_error
from src.engine.graphrag._version_match import find_version_candidate
from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    GraphLink,
    GraphNode,
    IngestSource,
    RecallChunk,
    RecallRequest,
    RecallResult,
)
from src.engine.hindsight_components.enrich import MemoryStateEnricher
from src.engine.hindsight_components.hook import build_retain_hook
from src.engine.scope import MemoryScope, TagFilter

# Uploaded document originals; settings-driven (UPLOADS_DIR) so the compose
# deployment can point at its named volume while the default stays relative.
UPLOAD_DIR = Path(settings.uploads_dir)

logger = logging.getLogger(__name__)

# 后台 ingest/reindex 任务的强引用注册表：asyncio 只持有任务的弱引用，
# 无引用的任务可能在执行中被 GC。调度时加入，完成回调中移除。
_background_tasks: set[asyncio.Task] = set()


async def _mark_document_failed(document_id: uuid.UUID, error: str) -> None:
    """best-effort：把意外死亡的后台任务对应的文档行标记为 failed。"""
    async with async_session_factory() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="failed", error_msg=error)
        )
        await session.commit()


def _recovery_done_callback(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("标记文档 failed 状态时出错", exc_info=error)


def _make_background_done_callback(
    document_id: uuid.UUID, label: str
) -> Callable[[asyncio.Task], None]:
    def _callback(task: asyncio.Task) -> None:
        _background_tasks.discard(task)
        if task.cancelled():
            logger.info("后台任务已取消: %s（文档 %s）", label, document_id)
            return
        error = task.exception()
        if error is None:
            return
        # 任务在 pipeline 自身的失败收尾之外异常终止（如启动即失败）：
        # 记录日志并 best-effort 标记文档行 failed，避免静默死亡或
        # 永久 pending。
        logger.error(
            "后台任务 %s 异常终止（文档 %s）", label, document_id, exc_info=error
        )
        recovery = asyncio.create_task(
            _mark_document_failed(document_id, format_error(error))
        )
        _background_tasks.add(recovery)
        recovery.add_done_callback(_recovery_done_callback)

    return _callback


def _schedule_background(
    coro: Coroutine[Any, Any, Any], *, document_id: uuid.UUID, label: str
) -> asyncio.Task:
    """调度后台任务并持有强引用 + 挂失败收尾回调（见 _background_tasks）。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_make_background_done_callback(document_id, label))
    return task


def _remove_upload_directory(
    document_id: uuid.UUID,
    upload_dir: Path = UPLOAD_DIR,
) -> None:
    """Remove only the UUID-scoped upload directory, never a stored path."""
    doc_dir = upload_dir / str(document_id)
    if doc_dir.is_symlink():
        doc_dir.unlink()
    elif doc_dir.is_dir():
        shutil.rmtree(doc_dir)


def _safe_filename(name: str) -> str:
    """Collapse a caller-supplied name to a single path component.

    Strips any directory or relative-path component so a multipart filename
    cannot nest into an uncreated subdirectory (FileNotFoundError) or escape
    the per-doc upload directory (path traversal). Falls back to "document"
    when the name has no usable component.
    """
    base = Path(name).name
    if base in ("", ".", ".."):
        return "document"
    return base


def _to_ref(
    doc: Document, chunk_count: int = 0, overview: str | None = None
) -> DocumentRef:
    return DocumentRef(
        id=str(doc.id),
        title=doc.title,
        file_type=doc.file_type,
        status=doc.status,
        overview=overview if overview is not None else (doc.overview or ""),
        error_msg=doc.error_msg,
        version_group=str(getattr(doc, "version_group", "") or ""),
        version_number=getattr(doc, "version_number", 1),
        is_current=getattr(doc, "is_current", True),
    )


class GraphRAGBackend:
    """GraphRAG implementation of KnowledgeBase."""

    capabilities = Capabilities(graph=True, partial_update=True, multimodal=True)

    def __init__(
        self,
        neo4j: Neo4jClient,
        pipeline: Pipeline,
        state_enricher: MemoryStateEnricher | None = None,
        *,
        scope: MemoryScope | None = None,
        write_tags: tuple[str, ...] = (),
    ) -> None:
        self._neo4j = neo4j
        self._pipeline = pipeline
        self._enricher = state_enricher
        self.scope = scope or MemoryScope()
        self._write_tags = TagFilter(write_tags).tags
        if hasattr(neo4j, "with_scope"):
            self._neo4j = neo4j.with_scope(self.scope)

    async def _enrich(self, ref: DocumentRef) -> DocumentRef:
        if self._enricher is not None:
            await self._enricher.enrich_ref(ref)
        return ref

    def with_scope(self, scope: MemoryScope, *, write_tags: tuple[str, ...] = ()):
        return GraphRAGBackend(
            self._neo4j,
            self._pipeline,
            self._enricher.with_scope(scope) if self._enricher is not None else None,
            scope=scope,
            write_tags=write_tags,
        )

    # ── ingest / reingest / remove ───────────────────────────────

    async def ingest(self, source: IngestSource) -> DocumentRef:
        ref, _task = await self._ingest_one(source)
        return await self._enrich(ref)

    async def ingest_batch(self, sources: list[IngestSource]) -> list[DocumentRef]:
        """批量入库：逐文件隔离失败，其余文件继续。

        pipeline 任务并发执行，由 Pipeline 的 doc 信号量限流。
        """
        refs: list[DocumentRef] = []
        for source in sources:
            try:
                ref, _task = await self._ingest_one(source)
            except Exception as exc:  # 单文件失败不影响其余文件
                logger.exception("批量入库文件 %s 失败", source.name)
                ref = DocumentRef(
                    id="",
                    title=source.name,
                    file_type=ExtractorRegistry.guess_file_type(Path(source.name)),
                    status="failed",
                    error_msg=str(exc),
                )
            refs.append(await self._enrich(ref))
        return refs

    async def _ingest_one(
        self, source: IngestSource
    ) -> tuple[DocumentRef, asyncio.Task]:
        if not self.scope.permits(self.scope.bank_id, self._write_tags):
            raise ValueError("document write tags are outside the trusted scope")
        data = source.data
        if source.path is not None and not data:
            data = source.path.read_bytes()
        file_type = ExtractorRegistry.guess_file_type(Path(source.name))

        # Version identity is based on extracted text for every input format.
        # Hashing container bytes (.docx/.pdf) makes a no-op edit/upload look
        # different because metadata and compression bytes are unstable.
        extract_dir = UPLOAD_DIR / str(uuid.uuid4())
        extract_dir.mkdir(parents=True, exist_ok=True)
        extract_path = extract_dir / _safe_filename(source.name)
        extract_path.write_bytes(data)
        try:
            new_text = await asyncio.to_thread(registry.extract, extract_path)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

        # 版本链检测：同名文档的当前版本存在时，本次上传成为新版本。
        content_hash = hashlib.sha256(new_text.encode()).hexdigest()
        async with async_session_factory() as session:
            parent = (
                await session.execute(
                    select(Document)
                    .where(
                        public_document_filter(self.scope),
                        Document.tags == list(self._write_tags),
                        Document.title == source.name,
                        Document.is_current.is_(True),
                    )
                    .order_by(Document.created_at.desc())
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()

            # 改名识别兜底：标题不匹配时，用内容相似度在当前版中
            # 找疑似同一文档的候选。exact_content=True（纯重命名）自动
            # 挂链；仅相似则返回候选由调用方确认，不自动挂。
            version_match = None
            if parent is None:
                if new_text:
                    text_length = len(new_text)
                    existing = (
                        await session.execute(
                            select(Document.id, Document.title, Document.raw_text)
                            .where(
                                Document.is_current.is_(True),
                                public_document_filter(self.scope),
                                Document.tags == list(self._write_tags),
                                Document.status == "indexed",
                                Document.file_type == file_type,
                                func.length(Document.raw_text)
                                >= max(1, text_length // 2),
                                func.length(Document.raw_text) <= text_length * 2,
                            )
                            .order_by(Document.updated_at.desc())
                            .limit(200)
                        )
                    ).all()
                    version_match = await asyncio.to_thread(
                        find_version_candidate,
                        source.name,
                        new_text,
                        [(str(r.id), r.title, r.raw_text or "") for r in existing],
                    )
                    if version_match is not None and version_match.exact_content:
                        parent = await session.get(
                            Document, uuid.UUID(version_match.doc_id)
                        )

            if parent is not None and (
                parent.content_hash == content_hash or parent.raw_text == new_text
            ):
                # 内容与当前版本一致：不产生新版本，直接返回现有文档。
                # task 置为已完成（调用方仅持有引用，不 await）。
                ref = _to_ref(parent)
                loop = asyncio.get_running_loop()
                empty_task = loop.create_future()
                empty_task.set_result(None)
                return ref, empty_task

            doc_id = uuid.uuid4()
            doc_dir = UPLOAD_DIR / str(doc_id)
            doc_dir.mkdir(parents=True, exist_ok=True)
            file_path = doc_dir / _safe_filename(source.name)
            file_path.write_bytes(data)

            if parent is not None:
                # 挂入版本链：旧版让出 current，新版继承 version_group。
                await session.execute(
                    update(Document)
                    .where(Document.id == parent.id)
                    .values(is_current=False)
                )
                doc = Document(
                    bank_id=self.scope.bank_id,
                    tags=list(self._write_tags),
                    id=doc_id,
                    title=source.name,
                    file_type=file_type,
                    file_path=str(file_path),
                    status="pending",
                    version_group=parent.version_group,
                    version_number=parent.version_number + 1,
                    version_of=parent.id,
                )
            else:
                doc = Document(
                    bank_id=self.scope.bank_id,
                    tags=list(self._write_tags),
                    id=doc_id,
                    title=source.name,
                    file_type=file_type,
                    file_path=str(file_path),
                    status="pending",
                )
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
            ref = _to_ref(doc)

            # 版本链上下文（session 关闭前取出纯数据，避免 ORM 脱离会话）。
            previous_version = (
                VersionParent(
                    doc_id=str(parent.id),
                    raw_text=parent.raw_text or "",
                    from_version=parent.version_number,
                    to_version=doc.version_number,
                )
                if parent is not None and parent.raw_text
                else None
            )

        def _spawn() -> asyncio.Task:
            # 不变量：_ingest_one 把创建的 task 返回给调用方（ingest /
            # ingest_batch 均持有引用），任务不会被 GC，也无需注册表。
            # 重构时若改为不返回 task，必须改用 _schedule_background。
            if previous_version is not None:
                return asyncio.create_task(
                    self._pipeline.process_file(
                        doc_id,
                        file_path,
                        source.name,
                        file_type,
                        previous_version=previous_version,
                    )
                )
            return asyncio.create_task(
                self._pipeline.process_file(doc_id, file_path, source.name, file_type)
            )

        task = _spawn()
        if version_match is not None and parent is None:
            # 疑似改名的新版本：入库为独立文档，附候选供确认
            # （确认后可由调用方把它挂入候选的版本链）。
            ref = dataclasses.replace(ref, version_match=version_match.to_dict())
        return ref, task

    async def edit_content(self, doc_id: str, content: str) -> DocumentRef:
        """编辑保存（兼容入口）：保留可见性检查后委托版本化编辑。

        编辑不再原地覆盖，而是生成下一版本（历史保留 + 自动 diff）。
        """
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")
        return await self.edit_document(doc_id, content)

    async def edit_document(self, doc_id: str, new_text: str) -> DocumentRef:
        """版本化编辑：编辑保存 = 生成新版本，旧版保留在版本链中。

        当前版本退位（is_current=False），新行继承 version_group 并
        链接 version_of；内容 hash 一致时跳过（幂等）。
        """
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")
            if not doc.is_current:
                raise ValueError("只能编辑文档的当前版本")
            title = doc.title
            file_type = doc.file_type
            old_text = doc.raw_text or ""
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()

            if content_hash == doc.content_hash or new_text == old_text:
                # 内容未变：不产生新版本
                return await self._enrich(_to_ref(doc))

            # 新版本行
            new_id = uuid.uuid4()
            file_path = UPLOAD_DIR / str(new_id) / _safe_filename(title)
            new_doc = Document(
                bank_id=doc.bank_id,
                tags=list(doc.tags or []),
                id=new_id,
                title=title,
                file_type=file_type,
                file_path=str(file_path),
                raw_text=new_text,
                status="pending",
                version_group=doc.version_group,
                version_number=doc.version_number + 1,
                version_of=doc.id,
            )
            session.add(new_doc)
            await session.execute(
                update(Document).where(Document.id == uid).values(is_current=False)
            )
            await session.commit()

            previous_version = (
                VersionParent(
                    doc_id=str(uid),
                    raw_text=old_text,
                    from_version=doc.version_number,
                    to_version=doc.version_number + 1,
                )
                if old_text
                else None
            )

        # 同步保存原始文本到上传目录（供后续提取/下载）
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_text, encoding="utf-8")

        # 新版本行的重索引在后台执行：注册表持有强引用，意外死亡时
        # 标记的是新版本行（D4/N2）。
        _schedule_background(
            self._pipeline.reindex_document(
                new_id, new_text, previous_version=previous_version
            ),
            document_id=new_id,
            label="版本化编辑重建索引",
        )
        return await self._enrich(_to_ref(new_doc))

    async def reingest(self, doc_id: str) -> DocumentRef:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")
            new_text = doc.raw_text or ""
            file_path = Path(doc.file_path) if doc.file_path else None
            title = doc.title
            file_type = doc.file_type
            if not new_text and (file_path is None or not file_path.is_file()):
                raise ValueError("原始文件不存在，请重新上传文件")
            await session.execute(
                update(Document)
                .where(Document.id == uid)
                .values(status="pending", error_msg=None)
            )
            await session.commit()
            await session.refresh(doc)
            ref = _to_ref(doc)

        if new_text:
            _schedule_background(
                self._pipeline.reindex_document(uid, new_text),
                document_id=uid,
                label="重新处理",
            )
        else:
            assert file_path is not None
            _schedule_background(
                self._pipeline.process_file(uid, file_path, title, file_type),
                document_id=uid,
                label="重新处理",
            )
        return await self._enrich(ref)

    async def remove(self, doc_id: str) -> None:
        uid = uuid.UUID(doc_id)
        doc_existed = False
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if doc is not None and not is_public_document(doc, self.scope):
                return
            if doc is not None:
                doc_existed = True
                await self._pipeline.before_remove(doc_id)
                _remove_upload_directory(uid)
                await session.delete(doc)
                await session.commit()

        # 图谱清理无条件执行（幂等）：Postgres 行已删但图谱清理曾因
        # 死锁半途而废时，重试 remove 必须仍能清掉残留的图数据，
        # 否则产生永久孤儿节点（真实踩过：删除 500 后重试 200，
        # 但 if not doc: return 把清理挡在了门外）。
        await self._neo4j.delete_document_graph(doc_id)
        if not doc_existed:
            logger.info("文档 %s 不在 Postgres（可能已删），仍执行了图谱清理", doc_id)

    # ── 版本链查询（纵向迭代管理）─────────────────────────────────

    async def propose_edit(self, doc_id: str, edit_request: str) -> dict[str, Any]:
        """横向修改传播第一步：生成编辑提议（不落库）。

        流程（OneEdit 式 propose-validate）：
        1. 定位受影响 chunks（编辑请求与本文档 chunks 的向量相似度）
        2. LLM 生成修改后的完整文本提议
        3. 跨文档一致性检查：共享实体的关联文档
        返回提议，由用户确认后经 edit_document 落库（新版本）。
        """
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")
            title = doc.title
            raw_text = doc.raw_text or ""

        # 1. 受影响 chunks（编辑请求的向量近邻，只看本文档）
        affected: list[dict[str, Any]] = []
        try:
            query_embedding = await embedder.embed_text(edit_request)
            async with async_session_factory() as session:
                stmt = (
                    select(
                        Chunk.chunk_index,
                        Chunk.chunk_text,
                        (1 - Chunk.embedding.cosine_distance(query_embedding)).label(
                            "score"
                        ),
                    )
                    .where(Chunk.doc_id == uid, Chunk.embedding.is_not(None))
                    .order_by(Chunk.embedding.cosine_distance(query_embedding))
                    .limit(3)
                )
                rows = (await session.execute(stmt)).all()
                affected = [
                    {
                        "chunk_index": r.chunk_index,
                        "chunk_text": r.chunk_text[:300],
                        "relevance": round(float(r.score), 3),
                    }
                    for r in rows
                ]
        except Exception:
            logger.exception("受影响 chunk 定位失败，继续生成提议")

        # 2. LLM 编辑提议
        proposal = await self._pipeline.analyzer.propose_edit(
            raw_text, edit_request, title
        )

        # 3. 跨文档一致性检查
        related_docs: list[dict[str, Any]] = []
        try:
            related_docs = await self._neo4j.find_related_docs_via_entities(
                str(uid), limit=5
            )
        except Exception:
            logger.exception("跨文档关联检查失败，继续返回提议")

        return {
            "doc_id": doc_id,
            "title": title,
            "edit_request": edit_request,
            "affected_chunks": affected,
            "related_documents": related_docs,
            "proposed_text": proposal.proposed_text,
            "notes": proposal.notes,
            "next_step": (
                "确认提议后调用 edit_document_content(doc_id, content) 落库，"
                "将自动创建新版本并记录变更。"
            ),
        }

    async def list_versions(self, doc_id: str) -> list[dict[str, Any]]:
        """列出 doc 所在版本链的全部版本（按版本号升序）。"""
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")

            rows = (
                (
                    await session.execute(
                        select(Document)
                        .where(
                            Document.version_group == doc.version_group,
                            public_document_filter(self.scope),
                        )
                        .order_by(Document.version_number.asc())
                    )
                )
                .scalars()
                .all()
            )

            versions = []
            for d in rows:
                change_row = (
                    await session.execute(
                        select(DocumentChange)
                        .where(DocumentChange.doc_id == d.id)
                        .order_by(DocumentChange.to_version.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                versions.append(
                    {
                        "id": str(d.id),
                        "title": d.title,
                        "version_number": d.version_number,
                        "is_current": d.is_current,
                        "status": d.status,
                        "overview": (d.overview or "")[:200],
                        "change_summary": change_row.summary if change_row else "",
                        "created_at": d.created_at.isoformat()
                        if d.created_at
                        else None,
                    }
                )
            return versions

    async def diff_versions(
        self, doc_id: str, from_version: int, to_version: int
    ) -> dict[str, Any]:
        """返回两个版本间的结构化变更记录。"""
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                raise ValueError(f"文档不存在: {doc_id}")

            row = (
                await session.execute(
                    select(DocumentChange).where(
                        DocumentChange.doc_id == uid,
                        DocumentChange.from_version == from_version,
                        DocumentChange.to_version == to_version,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return {
                    "doc_id": doc_id,
                    "from_version": from_version,
                    "to_version": to_version,
                    "summary": "",
                    "changes": [],
                    "error": "版本变更记录不存在",
                }
            return {
                "doc_id": doc_id,
                "from_version": from_version,
                "to_version": to_version,
                "summary": row.summary,
                "changes": row.changes,
            }

    async def confirm_version_match(
        self, doc_id: str, parent_doc_id: str
    ) -> dict[str, Any]:
        """把改名识别的候选挂入版本链（用户确认动作）。"""
        uid = uuid.UUID(doc_id)
        pid = uuid.UUID(parent_doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            parent = await session.get(Document, pid)
            if doc is None or parent is None:
                raise ValueError(
                    f"文档不存在: {doc_id if doc is None else parent_doc_id}"
                )
            if not is_public_document(doc, self.scope) or not is_public_document(
                parent, self.scope
            ):
                raise ValueError("document not found")
            if (doc.bank_id, doc.tags) != (parent.bank_id, parent.tags):
                raise ValueError("version chain must share bank and tags")
            if doc.version_group == parent.version_group:
                return {
                    "doc_id": doc_id,
                    "parent_doc_id": parent_doc_id,
                    "already_linked": True,
                }
            if doc.is_current is False:
                raise ValueError(
                    f"文档 {doc_id} 自身不是当前版，不能挂为父文档的新版本"
                )

            group = parent.version_group
            next_number = (
                await session.execute(
                    select(func.max(Document.version_number)).where(
                        Document.version_group == group
                    )
                )
            ).scalar() or 0
            next_number += 1

            # 组内当前版全部退位
            await session.execute(
                update(Document)
                .where(Document.version_group == group, Document.is_current.is_(True))
                .values(is_current=False)
            )
            # 新文档入链并成为当前版
            await session.execute(
                update(Document)
                .where(Document.id == uid)
                .values(
                    version_group=group,
                    version_number=next_number,
                    version_of=pid,
                    is_current=True,
                )
            )
            await session.commit()

            previous_version = VersionParent(
                doc_id=str(pid),
                raw_text=parent.raw_text or "",
                from_version=parent.version_number,
                to_version=next_number,
            )
            doc_title = doc.title
            doc_text = doc.raw_text or ""

        # 补记 LLM diff + 版本图谱投影（复用入库管线）
        await self._pipeline.record_version_change(
            doc_id=uid,
            title=doc_title,
            new_text=doc_text,
            previous_version=previous_version,
        )
        # 修正两个 Document 节点的版本属性
        await self._neo4j.upsert_document_node(
            doc_id=str(pid),
            title=parent.title,
            file_type=parent.file_type,
            overview=parent.overview or "",
            version_number=parent.version_number,
            is_current=False,
        )
        await self._neo4j.upsert_document_node(
            doc_id=str(uid),
            title=doc_title,
            file_type=doc.file_type,
            overview=doc.overview or "",
            version_number=next_number,
            is_current=True,
        )

        return {
            "doc_id": doc_id,
            "parent_doc_id": parent_doc_id,
            "already_linked": False,
            "version_number": next_number,
        }

    # ── recall ───────────────────────────────────────────────────

    async def recall(self, request: RecallRequest) -> RecallResult:
        from src.engine.graphrag._search import full_search

        async with async_session_factory() as session:
            result = await full_search(
                session,
                self._neo4j,
                request.query,
                top_k=request.top_k,
                scope=self.scope,
            )
        chunks = [
            RecallChunk(
                doc_id=c.doc_id,
                title=c.title,
                chunk_text=c.chunk_text,
                reranker_score=c.reranker_score,
                vector_score=c.vector_score,
            )
            for c in result.chunks
        ]
        return RecallResult(
            chunks=chunks,
            related_entities=result.related_entities,
            related_docs=result.related_docs,
        )

    # ── graph ────────────────────────────────────────────────────

    async def get_graph(self, entity: str | None = None) -> GraphData:
        if entity is None:
            raw = await self._neo4j.get_full_graph()
        else:
            details = await self._neo4j.get_entity_details(entity)
            if not details:
                return GraphData()
            return GraphData(
                nodes=[
                    GraphNode(
                        name=details.name,
                        type=details.entity_type,
                        description=details.properties.get("description", ""),
                        sources=details.properties.get("sources", [])
                        if isinstance(details.properties.get("sources"), list)
                        else [],
                    )
                ],
                links=[
                    GraphLink(
                        source=r.get("other_name", ""),
                        target=details.name,
                        type=r.get("type", ""),
                        description=r.get("description", ""),
                    )
                    for r in details.relations
                    if r.get("type")
                ],
            )
        return GraphData(
            nodes=[
                GraphNode(
                    name=n["name"],
                    type=n["type"],
                    description=n.get("description", ""),
                    sources=n.get("sources", []),
                )
                for n in raw.get("nodes", [])
            ],
            links=[
                GraphLink(
                    source=link["source"],
                    target=link["target"],
                    type=link["type"],
                    description=link.get("description", ""),
                )
                for link in raw.get("links", [])
            ],
        )

    async def get_neighbors(self, entity: str, hops: int = 2) -> GraphData:
        results, links = await self._neo4j.query_neighbors(entity, hops=hops)
        return GraphData(
            nodes=[
                GraphNode(
                    name=r.name,
                    type=r.entity_type,
                    description=r.properties.get("description", ""),
                )
                for r in results
            ],
            links=[
                GraphLink(
                    source=link["source"],
                    target=link["target"],
                    type=link["type"],
                    description=link.get("description", ""),
                )
                for link in links
            ],
        )

    # ── browse ───────────────────────────────────────────────────

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        async with async_session_factory() as session:
            stmt = (
                select(Document)
                .where(public_document_filter(self.scope))
                .order_by(Document.created_at.desc())
            )
            if file_type:
                stmt = stmt.where(Document.file_type == file_type)
            if status:
                stmt = stmt.where(Document.status == status)

            count_stmt = select(func.count(Document.id)).where(
                public_document_filter(self.scope)
            )
            if file_type:
                count_stmt = count_stmt.where(Document.file_type == file_type)
            if status:
                count_stmt = count_stmt.where(Document.status == status)
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            docs = (await session.execute(stmt)).scalars().all()

            items: list[dict[str, Any]] = [
                {
                    "id": str(d.id),
                    "title": d.title,
                    "file_type": d.file_type,
                    "status": d.status,
                    "overview": (d.overview or "")[:200],
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                    "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                }
                for d in docs
            ]
        if self._enricher is not None:
            await self._enricher.enrich_dicts(items)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": items,
        }

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc or not is_public_document(doc, self.scope):
                return None
            count_stmt = select(func.count(Chunk.id)).where(Chunk.doc_id == uid)
            chunk_count = (await session.execute(count_stmt)).scalar() or 0
            result: dict[str, Any] = {
                "id": str(doc.id),
                "title": doc.title,
                "file_type": doc.file_type,
                "raw_text": doc.raw_text,
                "overview": doc.overview,
                "file_path": doc.file_path,
                "content_hash": doc.content_hash,
                "status": doc.status,
                "error_msg": doc.error_msg,
                "chunk_count": chunk_count,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            }
        if self._enricher is not None:
            await self._enricher.enrich_dict(result)
        return result


def build(config: EngineConfig) -> GraphRAGBackend:
    """Factory used by src.engine.config.build_engine."""

    from src.engine.components.store.source_graph import SourceNeo4jClient

    neo4j = SourceNeo4jClient()
    analyzer = Analyzer(schema_path=config.config_dir / "entity_schema.yaml")
    index_hook = config.index_hook
    enricher: MemoryStateEnricher | None = None
    if config.memory is not None:
        from src.engine.hindsight_components.repository import PostgresMemoryRepository

        repository = PostgresMemoryRepository(
            consolidation_enabled=config.memory.consolidation_enabled
        )
        enricher = MemoryStateEnricher(repository)
        if index_hook is None:
            index_hook = build_retain_hook(
                max_concurrent=config.memory.retain_max_concurrent,
                repository=repository,
            )
    from src.engine.components.file_summary import FileSummaryManager

    pipeline = Pipeline(
        neo4j,
        analyzer=analyzer,
        index_hook=index_hook,
        vector_only=config.ingest.vector_only,
        summary_manager=FileSummaryManager(async_session_factory, analyzer=analyzer),
        chunk_concurrency=config.ingest.chunk_concurrency,
        doc_concurrency=config.ingest.doc_concurrency,
        llm_retries=config.ingest.llm_retries,
        llm_backoff_base_seconds=config.ingest.llm_backoff_base_seconds,
    )
    return GraphRAGBackend(neo4j, pipeline, state_enricher=enricher)
