"""文件入库 Pipeline：提取 -> 分块 -> LLM 分析 -> Embedding -> 写入存储。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import uuid
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from src.engine.components.store.models import (
    Chunk,
    Document,
    DocumentChange,
    DocumentRetrieval,
)
from src.engine.components.store.neo4j import (
    Neo4jClient,
    EntityData,
    EntitySource,
    RelationData,
)
from src.engine.components.store.postgres import async_session_factory
from src.engine.components.analyzer import (
    Analyzer,
    AnalysisResult,
    ChunkAnalysisResult,
)
from src.engine.components.chunker import chunk_text
from src.engine.components.embedder import embedder
from src.engine.components.extractors.registry import registry
from src.engine.components.retry import retry_transient
from src.engine.graphrag.progress import clear_progress, set_progress
from src.engine.interface import DocumentIndexHook
from src.engine.scope import MemoryScope, TagFilter
from src.engine.hindsight_components.utils import lexical_tokens

logger = logging.getLogger(__name__)


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    """TaskGroup 会把单异常包成 ExceptionGroup，展开便于 error_msg 可读。"""
    if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
        return exc.exceptions[0]
    return exc


def _filename_of(file_path: str | None) -> str | None:
    """文档存储路径 → 检索视图用的文件名（无路径时为 None）。"""
    if not file_path:
        return None
    return file_path.rsplit("/", 1)[-1] or None


def format_error(exc: BaseException) -> str:
    """类型在前的错误信息：无消息异常（如取消）也能标识失败原因。"""
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class StaleDocumentGeneration(RuntimeError):
    """The document changed while an indexing generation was in flight."""


@dataclass(frozen=True)
class ProcessingFence:
    revision: int
    generation: UUID


@dataclass
class VersionParent:
    """新版本上传时的上一版上下文（backend 在会话关闭前提取的纯数据）。"""

    doc_id: str
    raw_text: str
    from_version: int
    to_version: int


class Pipeline:
    """文件入库 Pipeline 编排器。"""

    def __init__(
        self,
        neo4j: Neo4jClient,
        analyzer: Analyzer | None = None,
        index_hook: DocumentIndexHook | None = None,
        chunk_concurrency: int = 4,
        doc_concurrency: int = 2,
        vector_only: bool = False,
        summary_manager=None,
        llm_retries: int = 3,
        llm_backoff_base_seconds: float = 2.0,
    ) -> None:
        self._summary_manager = summary_manager
        self._vector_only = vector_only
        self._neo4j = neo4j
        self._analyzer = analyzer or Analyzer()
        self._index_hook = index_hook
        self._chunk_sem = asyncio.Semaphore(max(1, chunk_concurrency))
        self._doc_sem = asyncio.Semaphore(max(1, doc_concurrency))
        self._llm_retries = llm_retries
        self._llm_backoff_base_seconds = llm_backoff_base_seconds

    async def _with_retry(self, call, *, description: str):
        """模型调用统一走瞬时失败重试（engine.ingest.llm_retries 预算）。"""
        return await retry_transient(
            call,
            retries=self._llm_retries,
            backoff_base_seconds=self._llm_backoff_base_seconds,
            description=description,
        )

    async def _analyze_document(
        self,
        raw_text: str,
        title: str,
        doc_id: UUID,
        filename: str | None = None,
    ) -> tuple[
        AnalysisResult,
        list,
        list[ChunkAnalysisResult],
        list[float],
        list[list[float]],
    ]:
        """分块后并行执行：overview ∥ 逐 chunk 分析（信号量限流）∥ embedding。

        返回 (doc_analysis, chunks, chunk_analyses, embeddings)；
        chunk_analyses 按 chunk 顺序排列，写入顺序确定。
        chunk embedding 输入只使用原始文本；标题、文件名和 overview 由
        独立的文档级/字段级检索承担，避免把同一 metadata 重复进每个向量。
        """
        chunks = await asyncio.to_thread(chunk_text, raw_text)
        total = len(chunks)
        results: list[ChunkAnalysisResult | None] = [None] * total
        completed = 0
        set_progress(str(doc_id), "analyzing_chunks", f"分析实体 0/{total}", 0, total)

        async def analyze_one(index: int, text: str) -> None:
            nonlocal completed
            async with self._chunk_sem:
                ca = await self._with_retry(
                    lambda: self._analyzer.analyze_chunk(text, title, index),
                    description=f"chunk {index} 实体分析",
                )
            results[index] = ca
            completed += 1
            set_progress(
                str(doc_id),
                "analyzing_chunks",
                f"分析实体 {completed}/{total}",
                completed,
                total,
            )

        async def _no_embeddings() -> list[list[float]]:
            return []

        async def _embed_document_and_chunks() -> list[list[float]]:
            analysis = await overview_task
            parent_text = "\n".join(
                part
                for part in (
                    title,
                    filename or "",
                    analysis.overview,
                    " ".join(entity.name for entity in analysis.entities),
                )
                if part
            )
            return await self._with_retry(
                lambda: embedder.embed_batch([parent_text, *[c.text for c in chunks]]),
                description="embedding 批量生成",
            )

        async with asyncio.TaskGroup() as tg:
            overview_task = tg.create_task(
                self._summary_overview(raw_text, title, doc_id)
                if self._vector_only
                else self._with_retry(
                    lambda: self._analyzer.analyze_overview(raw_text, title),
                    description="document overview",
                )
            )
            embed_task = tg.create_task(_embed_document_and_chunks())
            for i, chunk in enumerate(chunks if not self._vector_only else []):
                tg.create_task(analyze_one(i, chunk.text))

        vectors = embed_task.result()
        return (
            overview_task.result(),
            chunks,
            [ca for ca in results if ca is not None],
            vectors[0],
            vectors[1:],
        )

    async def _begin_processing(
        self, doc_id: UUID, *, skip_complete: bool = False
    ) -> ProcessingFence | None:
        """Claim a document generation and make any prior parent row unreadable."""
        async with async_session_factory() as session:
            owner = await session.get(Document, doc_id, with_for_update=True)
            if owner is None:
                raise ValueError(f"文档不存在: {doc_id}")
            parent = await session.get(DocumentRetrieval, doc_id)
            revision = getattr(owner, "version_number", 1)
            if not getattr(owner, "is_current", True):
                if parent is not None:
                    parent.generation_state = "failed"
                await session.commit()
                raise StaleDocumentGeneration(f"文档 {doc_id} 已不是当前版本")
            if (
                skip_complete
                and owner.status == "indexed"
                and parent is not None
                and parent.revision == revision
                and parent.generation_state == "ready"
            ):
                return None
            generation = uuid.uuid4()
            owner.status = "processing"
            owner.error_msg = None
            owner.processing_generation = generation
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(
                    status="processing",
                    error_msg=None,
                    processing_generation=generation,
                )
            )
            if parent is not None:
                parent.generation_state = "pending"
            await session.commit()
            return ProcessingFence(revision=revision, generation=generation)

    async def _summary_overview(self, raw_text, title, doc_id):
        if self._summary_manager is None:
            return await self._analyzer.summarize_document(raw_text, title)
        summary = await self._summary_manager.prepare(str(doc_id), raw_text, title)
        return AnalysisResult(overview=summary.text)

    async def process_file(
        self,
        doc_id: UUID,
        file_path: Path,
        title: str,
        file_type: str,
        previous_version: VersionParent | None = None,
    ) -> None:
        """处理新上传的文件：提取 -> 分块 -> 分析 -> embedding -> 写入。

        幂等性：通过 content_hash (SHA256) 判断，内容未变则跳过。
        doc 信号量限制并发处理的文档数；分析阶段内部并行（chunk 信号量限流）。
        版本链：previous_version 提供上一版上下文时，成功入库后
        追加变更抽取（LLM diff）并写入版本图谱。
        """
        try:
            fence = await self._begin_processing(doc_id, skip_complete=True)
        except StaleDocumentGeneration:
            clear_progress(str(doc_id))
            logger.info("文档 %s 的过期 pipeline 已停止", doc_id)
            return
        if fence is None:
            logger.info(f"文档 {doc_id} 及其检索记录已完成，跳过 pipeline")
            return

        try:
            # 3. 提取 + 并行分析（doc 信号量限制并发文档数）
            set_progress(str(doc_id), "extracting", "提取文本")
            async with self._doc_sem:
                raw_text = await asyncio.to_thread(registry.extract, file_path)
                content_hash = hashlib.sha256(raw_text.encode()).hexdigest()
                logger.info(f"文档 {doc_id} 提取完成, {len(raw_text)} 字符")
                (
                    doc_analysis,
                    chunks,
                    chunk_analyses,
                    document_embedding,
                    embeddings,
                ) = await self._analyze_document(
                    raw_text, title, doc_id, filename=file_path.name
                )
            logger.info(
                f"文档 {doc_id} 分析完成: {len(chunks)} chunks, "
                f"{len(doc_analysis.file_relations)} file_relations"
            )

            # 4. 写入 Postgres
            async with async_session_factory() as session:
                await self._persist_chunks(
                    session,
                    doc_id=doc_id,
                    title=title,
                    raw_text=raw_text,
                    content_hash=content_hash,
                    overview=doc_analysis.overview,
                    filename=file_path.name,
                    entities=[entity.name for entity in doc_analysis.entities],
                    document_embedding=document_embedding,
                    chunks=chunks,
                    embeddings=embeddings,
                    fence=fence,
                )

            # 5. 写入 Neo4j（三层图谱）
            async with async_session_factory() as session:
                set_progress(str(doc_id), "writing_neo4j", "写入知识图谱")
                doc_row = await session.get(Document, doc_id)
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                    # getattr 容错：tests 用 SimpleNamespace 伪造 Document
                    version_number=getattr(doc_row, "version_number", 1)
                    if doc_row
                    else 1,
                    is_current=getattr(doc_row, "is_current", True)
                    if doc_row
                    else True,
                )
                await self._write_chunk_graph(str(doc_id), title, chunk_analyses)
                if doc_analysis.file_relations:
                    await self._write_file_relations(
                        str(doc_id), doc_analysis.file_relations, session
                    )
                # 6. 版本链：抽取相邻版本 diff + 版本图谱投影
                if previous_version is not None:
                    await self._process_version_change(
                        doc_id=doc_id,
                        title=title,
                        new_text=raw_text,
                        previous_version=previous_version,
                        session=session,
                    )
            await self._notify_indexed(
                document_id=str(doc_id),
                title=title,
                content=raw_text,
                file_type=file_type,
            )
            clear_progress(str(doc_id))
            logger.info(f"文档 {doc_id} Pipeline 完成 ✓")

        except StaleDocumentGeneration:
            clear_progress(str(doc_id))
            logger.info("文档 %s 的过期 pipeline 结果未发布", doc_id)
        except Exception as e:
            await self._mark_failed(doc_id, e, "Pipeline", fence=fence)

    async def _persist_chunks(
        self,
        session,
        *,
        doc_id: UUID,
        title: str,
        raw_text: str,
        content_hash: str,
        overview: str,
        filename: str,
        entities: list[str],
        document_embedding: list[float],
        chunks: list,
        embeddings: list[list[float]],
        fence: ProcessingFence,
    ) -> None:
        """chunk 行写入 + 文档状态更新为 indexed（两条入库路径共用）。"""
        set_progress(str(doc_id), "writing_postgres", "写入数据库")
        owner = await session.get(Document, doc_id, with_for_update=True)
        if owner is None:
            raise ValueError(f"文档不存在: {doc_id}")
        if (
            not getattr(owner, "is_current", True)
            or owner.status != "processing"
            or getattr(owner, "version_number", 1) != fence.revision
            or getattr(owner, "processing_generation", None) != fence.generation
        ):
            await session.rollback()
            raise StaleDocumentGeneration(
                f"文档 {doc_id} 的 revision/generation fence 已变化"
            )
        await session.execute(
            Chunk.__table__.delete().where(Chunk.doc_id == doc_id)  # type: ignore[union-attr]
        )
        parent_text = "\n".join(
            part
            for part in (
                title,
                filename,
                overview,
                " ".join(owner.tags or []),
                " ".join(entities),
            )
            if part
        )
        await session.execute(
            insert(DocumentRetrieval)
            .values(
                doc_id=doc_id,
                bank_id=owner.bank_id,
                revision=getattr(owner, "version_number", 1),
                title=title,
                filename=filename,
                overview=overview,
                tags=list(owner.tags or []),
                entities=list(dict.fromkeys(entities)),
                field_tokens=lexical_tokens(parent_text),
                embedding=document_embedding,
                embedding_model=getattr(embedder, "_model", ""),
                generation_state="ready",
            )
            .on_conflict_do_update(
                index_elements=[DocumentRetrieval.doc_id],
                set_={
                    "bank_id": owner.bank_id,
                    "revision": getattr(owner, "version_number", 1),
                    "title": title,
                    "filename": filename,
                    "overview": overview,
                    "tags": list(owner.tags or []),
                    "entities": list(dict.fromkeys(entities)),
                    "field_tokens": lexical_tokens(parent_text),
                    "embedding": document_embedding,
                    "embedding_model": getattr(embedder, "_model", ""),
                    "generation_state": "ready",
                },
            )
        )

        doc_uri = f"{doc_id}:{title}"
        for chunk, embedding in zip(chunks, embeddings):
            session.add(
                Chunk(
                    bank_id=owner.bank_id,
                    tags=list(owner.tags or []),
                    doc_id=doc_id,
                    chunk_index=chunk.index,
                    chunk_text=chunk.text,
                    embedding=embedding,
                    embedding_model=getattr(embedder, "_model", ""),
                    overview=overview,
                    doc_uri=doc_uri,
                    token_count=chunk.token_count,
                )
            )

        await session.execute(
            update(Document)
            .where(
                Document.id == doc_id,
                Document.is_current.is_(True),
                Document.version_number == fence.revision,
                Document.processing_generation == fence.generation,
            )
            .values(
                raw_text=raw_text,
                overview=overview,
                content_hash=content_hash,
                status="indexed",
                error_msg=None,
                processing_generation=fence.generation,
            )
        )
        await session.commit()
        logger.info(f"文档 {doc_id} Postgres 写入完成")

    async def _mark_failed(
        self,
        doc_id: UUID,
        exc: Exception,
        stage: str,
        *,
        fence: ProcessingFence | None = None,
    ) -> None:
        """失败收尾：清进度 + 状态置为 failed（error_msg 永不为空）。"""
        clear_progress(str(doc_id))
        unwrapped = _unwrap_exception_group(exc)
        logger.error(f"文档 {doc_id} {stage} 失败: {unwrapped}", exc_info=True)
        async with async_session_factory() as session:
            conditions = [Document.id == doc_id, Document.is_current.is_(True)]
            if fence is not None:
                conditions.extend(
                    (
                        Document.version_number == fence.revision,
                        Document.processing_generation == fence.generation,
                    )
                )
            result = await session.execute(
                update(Document)
                .where(*conditions)
                .values(status="failed", error_msg=format_error(unwrapped))
            )
            if getattr(result, "rowcount", 1):
                parent_conditions = [DocumentRetrieval.doc_id == doc_id]
                if fence is not None:
                    parent_conditions.append(
                        DocumentRetrieval.revision == fence.revision
                    )
                await session.execute(
                    update(DocumentRetrieval)
                    .where(*parent_conditions)
                    .values(generation_state="failed")
                )
            await session.commit()

    async def reindex_document(
        self,
        doc_id: UUID,
        new_text: str,
        previous_version: VersionParent | None = None,
    ) -> None:
        """编辑后重新索引：跳过文本提取，直接从文本开始分析。

        previous_version 提供上一版上下文时，成功入库后追加
        变更抽取（LLM diff）并写入版本图谱。
        """
        try:
            fence = await self._begin_processing(doc_id)
        except StaleDocumentGeneration:
            clear_progress(str(doc_id))
            logger.info("文档 %s 的过期 re-index 已停止", doc_id)
            return
        assert fence is not None
        async with async_session_factory() as session:
            doc = await session.get(Document, doc_id)
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")

            title = doc.title
            file_type = doc.file_type
            version_of = doc.version_of
            version_number = doc.version_number
            is_current = doc.is_current
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()

        try:
            # 并行分析（doc 信号量限制并发文档数）
            async with self._doc_sem:
                (
                    doc_analysis,
                    chunks,
                    chunk_analyses,
                    document_embedding,
                    embeddings,
                ) = await self._analyze_document(
                    new_text,
                    title,
                    doc_id,
                    filename=_filename_of(getattr(doc, "file_path", None)),
                )

            async with async_session_factory() as session:
                await self._persist_chunks(
                    session,
                    doc_id=doc_id,
                    title=title,
                    raw_text=new_text,
                    content_hash=content_hash,
                    overview=doc_analysis.overview,
                    filename=_filename_of(getattr(doc, "file_path", None)) or "",
                    entities=[entity.name for entity in doc_analysis.entities],
                    document_embedding=document_embedding,
                    chunks=chunks,
                    embeddings=embeddings,
                    fence=fence,
                )

                # 更新 Neo4j（先清理旧图谱数据，防止过时实体残留）
                set_progress(str(doc_id), "writing_neo4j", "写入知识图谱")
                await self._neo4j.delete_document_graph(str(doc_id))
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                    version_number=version_number,
                    is_current=is_current,
                )
                # 版本链边在 delete_document_graph 中被移除，此处重连
                if version_of is not None:
                    await self._neo4j.link_next_version(
                        from_doc_id=str(version_of), to_doc_id=str(doc_id)
                    )
                await self._write_chunk_graph(str(doc_id), title, chunk_analyses)

                # L3: file_relations -> Document↔Document 边
                if doc_analysis.file_relations:
                    await self._write_file_relations(
                        str(doc_id), doc_analysis.file_relations, session
                    )

                # L4: 版本链：抽取相邻版本 diff + 版本图谱投影
                if previous_version is not None:
                    await self._process_version_change(
                        doc_id=doc_id,
                        title=title,
                        new_text=new_text,
                        previous_version=previous_version,
                        session=session,
                    )

            await self._notify_indexed(
                document_id=str(doc_id),
                title=title,
                content=new_text,
                file_type=file_type,
            )
            clear_progress(str(doc_id))

            logger.info(f"文档 {doc_id} re-index 完成 ✓")

        except StaleDocumentGeneration:
            clear_progress(str(doc_id))
            logger.info("文档 %s 的过期 re-index 结果未发布", doc_id)
        except Exception as e:
            await self._mark_failed(doc_id, e, "re-index", fence=fence)

    async def refresh_document_retrieval(self, doc_id: UUID) -> bool:
        """Rebuild a current indexed parent after title/tag/overview metadata changes."""
        async with async_session_factory() as session:
            document = await session.get(Document, doc_id)
            parent = await session.get(DocumentRetrieval, doc_id)
            if (
                document is None
                or not document.is_current
                or document.status != "indexed"
            ):
                return False
            revision = document.version_number
            filename = _filename_of(document.file_path) or ""
            title = document.title
            overview = document.overview or ""
            tags = list(document.tags or [])
            entities = list(parent.entities or []) if parent is not None else []
        parent_text = "\n".join(
            part
            for part in (
                title,
                filename,
                overview,
                " ".join(tags),
                " ".join(entities),
            )
            if part
        )
        vectors = await embedder.embed_batch([parent_text])
        if len(vectors) != 1:
            raise ValueError("embedding provider returned an unexpected row count")
        async with async_session_factory() as session, session.begin():
            current = await session.get(Document, doc_id, with_for_update=True)
            if (
                current is None
                or not current.is_current
                or current.status != "indexed"
                or current.version_number != revision
                or current.title != title
                or (_filename_of(current.file_path) or "") != filename
                or (current.overview or "") != overview
                or list(current.tags or []) != tags
            ):
                return False
            await session.execute(
                insert(DocumentRetrieval)
                .values(
                    doc_id=doc_id,
                    bank_id=current.bank_id,
                    revision=revision,
                    title=title,
                    filename=filename,
                    overview=overview,
                    tags=tags,
                    entities=entities,
                    field_tokens=lexical_tokens(parent_text),
                    embedding=vectors[0],
                    embedding_model=getattr(embedder, "_model", ""),
                    generation_state="ready",
                )
                .on_conflict_do_update(
                    index_elements=[DocumentRetrieval.doc_id],
                    set_={
                        "bank_id": current.bank_id,
                        "revision": revision,
                        "title": title,
                        "filename": filename,
                        "overview": overview,
                        "tags": tags,
                        "entities": entities,
                        "field_tokens": lexical_tokens(parent_text),
                        "embedding": vectors[0],
                        "embedding_model": getattr(embedder, "_model", ""),
                        "generation_state": "ready",
                    },
                )
            )
        return True

    async def record_version_change(
        self,
        doc_id: UUID,
        title: str,
        new_text: str,
        previous_version: VersionParent,
        session=None,
    ) -> None:
        """公开入口：为已入库的文档补记版本 diff 与图谱投影。

        confirm_version_match（改名确认挂链）等外部流程使用；
        session 缺省时自开一个。
        """
        if session is not None:
            await self._process_version_change(
                doc_id=doc_id,
                title=title,
                new_text=new_text,
                previous_version=previous_version,
                session=session,
            )
            return
        async with async_session_factory() as own_session:
            await self._process_version_change(
                doc_id=doc_id,
                title=title,
                new_text=new_text,
                previous_version=previous_version,
                session=own_session,
            )

    async def _process_version_change(
        self,
        doc_id: UUID,
        title: str,
        new_text: str,
        previous_version: VersionParent,
        session,
    ) -> None:
        """版本链入库：LLM diff -> document_changes 表 + Neo4j 版本图谱。

        失败只记录日志，不影响文档本身已成功的 indexed 状态。
        """
        try:
            # 1. LLM 抽取相邻版本的结构化变更
            analysis = await self._analyzer.analyze_changes(
                previous_version.raw_text, new_text, title
            )
            logger.info(
                f"文档 {doc_id} v{previous_version.from_version}"
                f"->v{previous_version.to_version} 变更抽取完成: "
                f"{len(analysis.changes)} changes"
            )

            # 2. 写入 document_changes（同版本对幂等覆盖）
            await session.execute(
                delete(DocumentChange).where(DocumentChange.doc_id == doc_id)
            )
            session.add(
                DocumentChange(
                    doc_id=doc_id,
                    from_version=previous_version.from_version,
                    to_version=previous_version.to_version,
                    summary=analysis.summary,
                    changes=analysis.changes,
                )
            )
            await session.commit()

            # 3. Neo4j 版本图谱投影
            await self._neo4j.link_next_version(
                from_doc_id=previous_version.doc_id, to_doc_id=str(doc_id)
            )
            await self._neo4j.upsert_changes(
                doc_id=str(doc_id),
                from_version=previous_version.from_version,
                to_version=previous_version.to_version,
                summary=analysis.summary,
                changes=analysis.changes,
            )
        except Exception:
            # 版本元数据是附加产物；失败不得回滚文档索引本身。
            logger.exception(f"文档 {doc_id} 版本链处理失败")

    async def _notify_indexed(
        self,
        *,
        document_id: str,
        title: str,
        content: str,
        file_type: str,
    ) -> None:
        if self._index_hook is None:
            return
        try:
            hook = await self._document_hook(document_id)
            await hook.after_indexed(
                document_id=document_id,
                title=title,
                content=content,
                file_type=file_type,
            )
        except Exception:
            # A secondary index must never change primary GraphRAG status.
            logger.exception("文档 %s 的附加索引钩子失败", document_id)

    async def before_remove(self, document_id: str) -> None:
        if self._index_hook is None:
            return
        try:
            hook = await self._document_hook(document_id)
            await hook.before_remove(document_id)
        except Exception:
            # Document FK cascade remains the final cleanup guarantee.
            logger.exception("文档 %s 的附加索引清理钩子失败", document_id)

    async def _document_hook(self, document_id: str):
        hook = self._index_hook
        if not hasattr(hook, "with_scope"):
            return hook
        async with async_session_factory() as session:
            owner = await session.get(Document, UUID(document_id))
            if owner is None:
                raise ValueError("document does not exist")
            tags = tuple(owner.tags or [])
            return hook.with_scope(
                MemoryScope(bank_id=owner.bank_id, visibility=TagFilter(tags, "exact")),
                write_tags=tags,
            )

    async def _write_chunk_graph(
        self,
        doc_id: str,
        title: str,
        chunk_analyses: list[ChunkAnalysisResult],
    ) -> None:
        """L1+L2: 扁平化 chunk 分析结果，批量写入实体和关系（MERGE 自然聚合）。"""
        entity_items: list[tuple[EntityData, EntitySource]] = []
        relation_items: list[tuple[RelationData, EntitySource]] = []
        for ca in chunk_analyses:
            source = EntitySource(
                doc_id=doc_id, chunk_index=ca.chunk_index, doc_title=title
            )
            entity_items.extend(
                (
                    EntityData(
                        name=entity.name,
                        entity_type=entity.type,
                        description=entity.description,
                    ),
                    source,
                )
                for entity in ca.entities
            )
            relation_items.extend(
                (
                    RelationData(
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        relation_type=relation.type,
                        description=relation.description,
                    ),
                    source,
                )
                for relation in ca.relations
            )
        await self._neo4j.upsert_entities_batch(entity_items)
        await self._neo4j.upsert_relations_batch(relation_items)

    async def _write_file_relations(
        self, doc_id: str, file_relations: list, session
    ) -> None:
        """解析 file_relations 并写入 Document↔Document 边。"""
        owner = await session.get(Document, UUID(doc_id))
        if owner is None:
            raise ValueError("document does not exist")
        for fr in file_relations:
            target_title = fr.related_doc_title
            if not target_title:
                continue

            # 通过 Postgres 按 title 查找目标文档
            result = await session.execute(
                select(Document.id)
                .where(
                    Document.title == target_title,
                    Document.bank_id == owner.bank_id,
                    Document.tags.contains(owner.tags or []),
                    Document.tags.contained_by(owner.tags or []),
                    Document.id != UUID(doc_id),
                )
                .order_by(Document.id)
                .limit(1)
            )
            target_doc = result.scalar_one_or_none()

            if target_doc is None:
                logger.info(f"file_relation 目标不存在: {target_title}, 跳过")
                continue

            await self._neo4j.create_doc_relation(
                source_doc_id=doc_id,
                target_doc_id=str(target_doc),
                relation_type=fr.type,
                reason=fr.reason,
            )
            logger.info(f"file_relation: {doc_id} → {target_doc} ({fr.type})")
