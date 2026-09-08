"""文件入库 Pipeline：提取 -> 分块 -> LLM 分析 -> Embedding -> 写入存储。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from src.engine.components.store.models import Chunk, Document
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
from src.engine.graphrag.progress import clear_progress, set_progress
from src.engine.interface import DocumentIndexHook

logger = logging.getLogger(__name__)


def _unwrap_exception_group(exc: BaseException) -> BaseException:
    """TaskGroup 会把单异常包成 ExceptionGroup，展开便于 error_msg 可读。"""
    if isinstance(exc, ExceptionGroup) and len(exc.exceptions) == 1:
        return exc.exceptions[0]
    return exc


class Pipeline:
    """文件入库 Pipeline 编排器。"""

    def __init__(
        self,
        neo4j: Neo4jClient,
        analyzer: Analyzer | None = None,
        index_hook: DocumentIndexHook | None = None,
        chunk_concurrency: int = 4,
        doc_concurrency: int = 2,
    ) -> None:
        self._neo4j = neo4j
        self._analyzer = analyzer or Analyzer()
        self._index_hook = index_hook
        self._chunk_sem = asyncio.Semaphore(max(1, chunk_concurrency))
        self._doc_sem = asyncio.Semaphore(max(1, doc_concurrency))

    async def _analyze_document(
        self, raw_text: str, title: str, doc_id: UUID
    ) -> tuple[
        AnalysisResult, list, list[ChunkAnalysisResult], list[list[float]]
    ]:
        """分块后并行执行：overview ∥ 逐 chunk 分析（信号量限流）∥ embedding。

        返回 (doc_analysis, chunks, chunk_analyses, embeddings)；
        chunk_analyses 按 chunk 顺序排列，写入顺序确定。
        """
        chunks = await asyncio.to_thread(chunk_text, raw_text)
        total = len(chunks)
        results: list[ChunkAnalysisResult | None] = [None] * total
        completed = 0
        set_progress(str(doc_id), "analyzing_chunks", f"分析实体 0/{total}", 0, total)

        async def analyze_one(index: int, text: str) -> None:
            nonlocal completed
            async with self._chunk_sem:
                ca = await self._analyzer.analyze_chunk(text, title, index)
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

        async with asyncio.TaskGroup() as tg:
            overview_task = tg.create_task(
                self._analyzer.analyze_overview(raw_text, title)
            )
            embed_task = tg.create_task(
                embedder.embed_batch([c.text for c in chunks])
                if chunks
                else _no_embeddings()
            )
            for i, chunk in enumerate(chunks):
                tg.create_task(analyze_one(i, chunk.text))

        return (
            overview_task.result(),
            chunks,
            [ca for ca in results if ca is not None],
            embed_task.result(),
        )

    async def process_file(
        self,
        doc_id: UUID,
        file_path: Path,
        title: str,
        file_type: str,
    ) -> None:
        """处理新上传的文件：提取 -> 分块 -> 分析 -> embedding -> 写入。

        幂等性：通过 content_hash (SHA256) 判断，内容未变则跳过。
        doc 信号量限制并发处理的文档数；分析阶段内部并行（chunk 信号量限流）。
        """
        async with async_session_factory() as session:
            # 1. 读取文件并计算 hash
            raw_bytes = file_path.read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()

            # 检查幂等性
            doc = await session.get(Document, doc_id)
            if doc and doc.content_hash == content_hash and doc.status == "indexed":
                logger.info(f"文档 {doc_id} 内容未变，跳过 pipeline")
                return

            # 2. 标记为 processing
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="processing", error_msg=None)
            )
            await session.commit()

        try:
            # 3. 提取 + 并行分析（doc 信号量限制并发文档数）
            set_progress(str(doc_id), "extracting", "提取文本")
            async with self._doc_sem:
                raw_text = await asyncio.to_thread(registry.extract, file_path)
                logger.info(f"文档 {doc_id} 提取完成, {len(raw_text)} 字符")
                (
                    doc_analysis,
                    chunks,
                    chunk_analyses,
                    embeddings,
                ) = await self._analyze_document(raw_text, title, doc_id)
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
                    chunks=chunks,
                    embeddings=embeddings,
                )

            # 5. 写入 Neo4j（三层图谱）
            async with async_session_factory() as session:
                set_progress(str(doc_id), "writing_neo4j", "写入知识图谱")
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                )
                await self._write_chunk_graph(str(doc_id), title, chunk_analyses)
                if doc_analysis.file_relations:
                    await self._write_file_relations(
                        str(doc_id), doc_analysis.file_relations, session
                    )
            await self._notify_indexed(
                document_id=str(doc_id),
                title=title,
                content=raw_text,
                file_type=file_type,
            )
            clear_progress(str(doc_id))
            logger.info(f"文档 {doc_id} Pipeline 完成 ✓")

        except Exception as e:
            await self._mark_failed(doc_id, e, "Pipeline")

    async def _persist_chunks(
        self,
        session,
        *,
        doc_id: UUID,
        title: str,
        raw_text: str,
        content_hash: str,
        overview: str,
        chunks: list,
        embeddings: list[list[float]],
    ) -> None:
        """chunk 行写入 + 文档状态更新为 indexed（两条入库路径共用）。"""
        set_progress(str(doc_id), "writing_postgres", "写入数据库")
        await session.execute(
            Chunk.__table__.delete().where(Chunk.doc_id == doc_id)  # type: ignore[union-attr]
        )

        doc_uri = f"{doc_id}:{title}"
        for chunk, embedding in zip(chunks, embeddings):
            session.add(
                Chunk(
                    doc_id=doc_id,
                    chunk_index=chunk.index,
                    chunk_text=chunk.text,
                    embedding=embedding,
                    overview=overview,
                    doc_uri=doc_uri,
                    token_count=chunk.token_count,
                )
            )

        await session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(
                raw_text=raw_text,
                overview=overview,
                content_hash=content_hash,
                status="indexed",
                error_msg=None,
            )
        )
        await session.commit()
        logger.info(f"文档 {doc_id} Postgres 写入完成")

    async def _mark_failed(self, doc_id: UUID, exc: Exception, stage: str) -> None:
        """失败收尾：清进度 + 状态置为 failed。"""
        clear_progress(str(doc_id))
        unwrapped = _unwrap_exception_group(exc)
        logger.error(f"文档 {doc_id} {stage} 失败: {unwrapped}", exc_info=True)
        async with async_session_factory() as session:
            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="failed", error_msg=str(unwrapped))
            )
            await session.commit()

    async def reindex_document(self, doc_id: UUID, new_text: str) -> None:
        """编辑后重新索引：跳过文本提取，直接从文本开始分析。"""
        async with async_session_factory() as session:
            doc = await session.get(Document, doc_id)
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")

            title = doc.title
            file_type = doc.file_type
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()

            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="processing", error_msg=None)
            )
            await session.commit()

        try:
            # 并行分析（doc 信号量限制并发文档数）
            async with self._doc_sem:
                (
                    doc_analysis,
                    chunks,
                    chunk_analyses,
                    embeddings,
                ) = await self._analyze_document(new_text, title, doc_id)

            async with async_session_factory() as session:
                await self._persist_chunks(
                    session,
                    doc_id=doc_id,
                    title=title,
                    raw_text=new_text,
                    content_hash=content_hash,
                    overview=doc_analysis.overview,
                    chunks=chunks,
                    embeddings=embeddings,
                )

                # 更新 Neo4j（先清理旧图谱数据，防止过时实体残留）
                set_progress(str(doc_id), "writing_neo4j", "写入知识图谱")
                await self._neo4j.delete_document_graph(str(doc_id))
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                )
                await self._write_chunk_graph(str(doc_id), title, chunk_analyses)

                # L3: file_relations -> Document↔Document 边
                if doc_analysis.file_relations:
                    await self._write_file_relations(
                        str(doc_id), doc_analysis.file_relations, session
                    )

            await self._notify_indexed(
                document_id=str(doc_id),
                title=title,
                content=new_text,
                file_type=file_type,
            )
            clear_progress(str(doc_id))

            logger.info(f"文档 {doc_id} re-index 完成 ✓")

        except Exception as e:
            await self._mark_failed(doc_id, e, "re-index")

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
            await self._index_hook.after_indexed(
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
            await self._index_hook.before_remove(document_id)
        except Exception:
            # Document FK cascade remains the final cleanup guarantee.
            logger.exception("文档 %s 的附加索引清理钩子失败", document_id)

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
        for fr in file_relations:
            target_title = fr.related_doc_title
            if not target_title:
                continue

            # 通过 Postgres 按 title 查找目标文档
            result = await session.execute(
                select(Document.id).where(Document.title == target_title).limit(1)
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
