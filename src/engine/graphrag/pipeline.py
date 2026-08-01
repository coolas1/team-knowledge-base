"""文件入库 Pipeline：提取 → 分块 → LLM 分析 → Embedding → 写入存储。"""

from __future__ import annotations

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
from src.engine.components.analyzer import Analyzer, ChunkAnalysisResult
from src.engine.components.chunker import chunk_text
from src.engine.components.embedder import embedder
from src.engine.components.extractors.registry import registry
from src.engine.interface import DocumentIndexHook

logger = logging.getLogger(__name__)


class Pipeline:
    """文件入库 Pipeline 编排器。"""

    def __init__(
        self,
        neo4j: Neo4jClient,
        analyzer: Analyzer | None = None,
        index_hook: DocumentIndexHook | None = None,
    ) -> None:
        self._neo4j = neo4j
        self._analyzer = analyzer or Analyzer()
        self._index_hook = index_hook

    async def process_file(
        self,
        doc_id: UUID,
        file_path: Path,
        title: str,
        file_type: str,
    ) -> None:
        """处理新上传的文件：提取 → 分块 → 分析 → embedding → 写入。

        幂等性：通过 content_hash (SHA256) 判断，内容未变则跳过。
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
                # 3. 文本提取
                raw_text = registry.extract(file_path)
                logger.info(f"文档 {doc_id} 提取完成, {len(raw_text)} 字符")

                # 4. 文本分块（先分块再分析）
                chunks = chunk_text(raw_text)
                logger.info(f"文档 {doc_id} 分块完成: {len(chunks)} chunks")

                # 5. 文档级 overview + file_relations
                doc_analysis = await self._analyzer.analyze_overview(raw_text, title)
                logger.info(
                    f"文档 {doc_id} overview 生成完成, "
                    f"{len(doc_analysis.file_relations)} file_relations"
                )

                # 6. 逐 Chunk LLM 分析
                chunk_analyses: list[ChunkAnalysisResult] = []
                for chunk in chunks:
                    ca = await self._analyzer.analyze_chunk(
                        chunk.text, title, chunk.index
                    )
                    chunk_analyses.append(ca)
                    logger.info(
                        f"文档 {doc_id} chunk[{chunk.index}]: "
                        f"{len(ca.entities)} 实体, {len(ca.relations)} 关系"
                    )

                # 7. Embedding
                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

                # 8. 写入 Postgres（overview 使用 doc_analysis.overview）
                await session.execute(
                    Chunk.__table__.delete().where(Chunk.doc_id == doc_id)  # type: ignore[union-attr]
                )

                doc_uri = f"{doc_id}:{title}"
                for chunk, embedding in zip(chunks, embeddings):
                    db_chunk = Chunk(
                        doc_id=doc_id,
                        chunk_index=chunk.index,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        overview=doc_analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(
                        raw_text=raw_text,
                        overview=doc_analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        error_msg=None,
                    )
                )
                await session.commit()
                logger.info(f"文档 {doc_id} Postgres 写入完成")

                # 9. 写入 Neo4j（三层图谱）
                await self._write_graph(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=doc_analysis.overview,
                    chunk_analyses=chunk_analyses,
                    file_relations=doc_analysis.file_relations,
                    session=session,
                )
                await self._notify_indexed(
                    document_id=str(doc_id),
                    title=title,
                    content=raw_text,
                    file_type=file_type,
                )
                logger.info(f"文档 {doc_id} Pipeline 完成 ✓")

            except Exception as e:
                logger.error(f"文档 {doc_id} Pipeline 失败: {e}", exc_info=True)
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(status="failed", error_msg=str(e))
                )
                await session.commit()

    async def reindex_document(self, doc_id: UUID, new_text: str) -> None:
        """编辑后重新索引：跳过文本提取，直接从文本开始分析。"""
        async with async_session_factory() as session:
            doc = await session.get(Document, doc_id)
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")

            title = doc.title
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()

            await session.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(status="processing", error_msg=None)
            )
            await session.commit()

            try:
                # 重新分块 + 逐 chunk 分析
                chunks = chunk_text(new_text)
                chunk_analyses: list[ChunkAnalysisResult] = []
                for chunk in chunks:
                    ca = await self._analyzer.analyze_chunk(
                        chunk.text, title, chunk.index
                    )
                    chunk_analyses.append(ca)

                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

                await session.execute(
                    Chunk.__table__.delete().where(Chunk.doc_id == doc_id)  # type: ignore[union-attr]
                )

                # 更新 overview
                doc_analysis = await self._analyzer.analyze_overview(new_text, title)

                doc_uri = f"{doc_id}:{title}"
                for chunk, embedding in zip(chunks, embeddings):
                    db_chunk = Chunk(
                        doc_id=doc_id,
                        chunk_index=chunk.index,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        overview=doc_analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(
                        raw_text=new_text,
                        overview=doc_analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        error_msg=None,
                    )
                )
                await session.commit()

                # 更新 Neo4j（先清理旧图谱数据，防止过时实体残留）
                await self._neo4j.delete_document_graph(str(doc_id))
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=doc.file_type,
                    overview=doc_analysis.overview,
                )
                # 写入 chunk 级实体和关系
                for ca in chunk_analyses:
                    source = EntitySource(
                        doc_id=str(doc_id), chunk_index=ca.chunk_index, doc_title=title
                    )
                    for entity in ca.entities:
                        await self._neo4j.upsert_entity(
                            entity=EntityData(
                                name=entity.name,
                                entity_type=entity.type,
                                description=entity.description,
                            ),
                            source=source,
                        )
                    for relation in ca.relations:
                        await self._neo4j.upsert_relation(
                            relation=RelationData(
                                from_name=relation.from_name,
                                to_name=relation.to_name,
                                relation_type=relation.type,
                                description=relation.description,
                            ),
                            source=source,
                        )

                # L3: file_relations → Document↔Document 边
                if doc_analysis.file_relations:
                    await self._write_file_relations(
                        str(doc_id), doc_analysis.file_relations, session
                    )

                await self._notify_indexed(
                    document_id=str(doc_id),
                    title=title,
                    content=new_text,
                    file_type=doc.file_type,
                )

                logger.info(f"文档 {doc_id} re-index 完成 ✓")

            except Exception as e:
                logger.error(f"文档 {doc_id} re-index 失败: {e}", exc_info=True)
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(status="failed", error_msg=str(e))
                )
                await session.commit()

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

    async def _write_graph(
        self,
        doc_id: str,
        title: str,
        file_type: str,
        overview: str,
        chunk_analyses: list[ChunkAnalysisResult],
        file_relations: list,
        session,
    ) -> None:
        """三层图谱写入：L1 chunk 级 + L2 文档内聚合 + L3 跨文档关联。"""
        # Document 节点（仅元数据）
        await self._neo4j.upsert_document_node(
            doc_id=doc_id, title=title, file_type=file_type, overview=overview
        )

        # L1+L2: 逐 chunk 写入实体和关系（MERGE 自然聚合）
        for ca in chunk_analyses:
            source = EntitySource(
                doc_id=doc_id, chunk_index=ca.chunk_index, doc_title=title
            )

            # 实体节点
            for entity in ca.entities:
                await self._neo4j.upsert_entity(
                    entity=EntityData(
                        name=entity.name,
                        entity_type=entity.type,
                        description=entity.description,
                    ),
                    source=source,
                )

            # 关系边
            for relation in ca.relations:
                await self._neo4j.upsert_relation(
                    relation=RelationData(
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        relation_type=relation.type,
                        description=relation.description,
                    ),
                    source=source,
                )

        # L3: file_relations → Document↔Document 边
        if file_relations:
            await self._write_file_relations(doc_id, file_relations, session)

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
