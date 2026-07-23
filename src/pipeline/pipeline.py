"""文件入库 Pipeline：提取 → 分块 → LLM 分析 → Embedding → 写入存储。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select, update

from src.db.models import (
    Chunk,
    Document,
    DocumentRelation,
    ExtractedEntity,
    ExtractedRelation,
    OutboxEvent,
)
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import async_session_factory
from src.pipeline.analyzer import analyzer, ChunkAnalysisResult
from src.pipeline.chunker import chunk_text
from src.pipeline.embedder import embedder
from src.pipeline.extractors.registry import registry

logger = logging.getLogger(__name__)


class Pipeline:
    """文件入库 Pipeline 编排器。"""

    def __init__(self, neo4j: Neo4jClient) -> None:
        self._neo4j = neo4j

    async def process_file(
        self,
        doc_id: UUID,
        file_path: Path,
        title: str,
        file_type: str,
        team_id: str = "default",
    ) -> None:
        """处理新上传的文件：提取 → 分块 → 分析 → embedding → 写入。

        幂等性：通过 content_hash (SHA256) 判断，内容未变则跳过。
        """
        async with async_session_factory() as session:
            # 1. 读取文件并计算 hash
            raw_bytes = file_path.read_bytes()
            content_hash = hashlib.sha256(raw_bytes).hexdigest()

            # 检查幂等性
            doc = await session.scalar(
                select(Document).where(Document.id == doc_id, Document.team_id == team_id)
            )
            if not doc:
                raise ValueError(f"文档不存在或不属于 team {team_id}: {doc_id}")
            if doc and doc.content_hash == content_hash and doc.status == "indexed":
                logger.info(f"文档 {doc_id} 内容未变，跳过 pipeline")
                return

            # 2. 标记为 processing
            await session.execute(
                update(Document)
                .where(Document.id == doc_id, Document.team_id == team_id)
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
                doc_analysis = await analyzer.analyze_overview(raw_text, title)
                logger.info(
                    f"文档 {doc_id} overview 生成完成, "
                    f"{len(doc_analysis.file_relations)} file_relations"
                )

                # 6. 逐 Chunk LLM 分析
                chunk_analyses: list[ChunkAnalysisResult] = []
                for chunk in chunks:
                    ca = await analyzer.analyze_chunk(
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
                    Chunk.__table__.delete().where(  # type: ignore[union-attr]
                        Chunk.doc_id == doc_id, Chunk.team_id == team_id
                    )
                )

                doc_uri = f"{doc_id}:{title}"
                for chunk, embedding in zip(chunks, embeddings):
                    db_chunk = Chunk(
                        team_id=team_id,
                        doc_id=doc_id,
                        chunk_index=chunk.index,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        overview=doc_analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                document_version = doc.version or 1
                await self._persist_knowledge_facts(
                    session,
                    team_id=team_id,
                    doc_id=doc_id,
                    document_version=document_version,
                    chunk_analyses=chunk_analyses,
                    file_relations=doc_analysis.file_relations,
                )

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id, Document.team_id == team_id)
                    .values(
                        raw_text=raw_text,
                        overview=doc_analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        graph_status="pending",
                        error_msg=None,
                    )
                )
                session.add(
                    OutboxEvent(
                        team_id=team_id,
                        aggregate_type="document",
                        aggregate_id=str(doc_id),
                        aggregate_version=document_version,
                        event_type="document_graph_upsert_requested",
                        payload={"document_id": str(doc_id)},
                    )
                )
                await session.commit()
                logger.info(f"文档 {doc_id} Postgres + Outbox 写入完成")

            except Exception as e:
                logger.error(f"文档 {doc_id} Pipeline 失败: {e}", exc_info=True)
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id, Document.team_id == team_id)
                    .values(status="failed", error_msg=str(e))
                )
                await session.commit()
                raise

    async def reindex_document(
        self, doc_id: UUID, new_text: str, team_id: str = "default"
    ) -> None:
        """编辑后重新索引：跳过文本提取，直接从文本开始分析。"""
        async with async_session_factory() as session:
            doc = await session.scalar(
                select(Document).where(Document.id == doc_id, Document.team_id == team_id)
            )
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")

            title = doc.title
            content_hash = hashlib.sha256(new_text.encode()).hexdigest()

            await session.execute(
                update(Document)
                .where(Document.id == doc_id, Document.team_id == team_id)
                .values(status="processing", error_msg=None)
            )
            await session.commit()

            try:
                # 重新分块 + 逐 chunk 分析
                chunks = chunk_text(new_text)
                chunk_analyses: list[ChunkAnalysisResult] = []
                for chunk in chunks:
                    ca = await analyzer.analyze_chunk(chunk.text, title, chunk.index)
                    chunk_analyses.append(ca)

                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

                await session.execute(
                    Chunk.__table__.delete().where(  # type: ignore[union-attr]
                        Chunk.doc_id == doc_id, Chunk.team_id == team_id
                    )
                )

                # 更新 overview
                doc_analysis = await analyzer.analyze_overview(new_text, title)

                doc_uri = f"{doc_id}:{title}"
                for chunk, embedding in zip(chunks, embeddings):
                    db_chunk = Chunk(
                        team_id=team_id,
                        doc_id=doc_id,
                        chunk_index=chunk.index,
                        chunk_text=chunk.text,
                        embedding=embedding,
                        overview=doc_analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                document_version = (doc.version or 1) + 1
                await self._persist_knowledge_facts(
                    session,
                    team_id=team_id,
                    doc_id=doc_id,
                    document_version=document_version,
                    chunk_analyses=chunk_analyses,
                    file_relations=doc_analysis.file_relations,
                )

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id, Document.team_id == team_id)
                    .values(
                        raw_text=new_text,
                        overview=doc_analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        version=document_version,
                        graph_status="pending",
                        error_msg=None,
                    )
                )
                session.add(
                    OutboxEvent(
                        team_id=team_id,
                        aggregate_type="document",
                        aggregate_id=str(doc_id),
                        aggregate_version=document_version,
                        event_type="document_graph_upsert_requested",
                        payload={"document_id": str(doc_id)},
                    )
                )
                await session.commit()
                logger.info(f"文档 {doc_id} re-index + Outbox 完成 ✓")

            except Exception as e:
                logger.error(f"文档 {doc_id} re-index 失败: {e}", exc_info=True)
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id, Document.team_id == team_id)
                    .values(status="failed", error_msg=str(e))
                )
                await session.commit()
                raise

    async def _persist_knowledge_facts(
        self,
        session,
        team_id: str,
        doc_id: UUID,
        document_version: int,
        chunk_analyses: list[ChunkAnalysisResult],
        file_relations: list,
    ) -> None:
        """将 LLM 抽取结果持久化为 PostgreSQL 权威知识事实。"""
        await session.execute(
            delete(ExtractedEntity).where(
                ExtractedEntity.doc_id == doc_id, ExtractedEntity.team_id == team_id
            )
        )
        await session.execute(
            delete(ExtractedRelation).where(
                ExtractedRelation.doc_id == doc_id, ExtractedRelation.team_id == team_id
            )
        )
        await session.execute(
            delete(DocumentRelation).where(
                DocumentRelation.source_doc_id == doc_id,
                DocumentRelation.team_id == team_id,
            )
        )

        for ca in chunk_analyses:
            for entity in ca.entities:
                if not entity.name:
                    continue
                session.add(
                    ExtractedEntity(
                        team_id=team_id,
                        doc_id=doc_id,
                        document_version=document_version,
                        chunk_index=ca.chunk_index,
                        name=entity.name,
                        normalized_name=entity.name.strip().casefold(),
                        entity_type=entity.type or "Unknown",
                        description=entity.description,
                    )
                )
            for relation in ca.relations:
                if not relation.from_name or not relation.to_name:
                    continue
                session.add(
                    ExtractedRelation(
                        team_id=team_id,
                        doc_id=doc_id,
                        document_version=document_version,
                        chunk_index=ca.chunk_index,
                        from_name=relation.from_name,
                        to_name=relation.to_name,
                        relation_type=relation.type or "RELATED_TO",
                        description=relation.description,
                    )
                )

        for fr in file_relations:
            target_title = fr.related_doc_title
            if not target_title:
                continue
            result = await session.execute(
                select(Document.id).where(
                    Document.team_id == team_id, Document.title == target_title
                ).limit(1)
            )
            target_doc = result.scalar_one_or_none()
            if target_doc is None:
                logger.info(f"file_relation 目标不存在: {target_title}, 跳过")
                continue
            session.add(
                DocumentRelation(
                    team_id=team_id,
                    source_doc_id=doc_id,
                    target_doc_id=target_doc,
                    relation_type=fr.type,
                    reason=fr.reason,
                )
            )
