"""文件入库 Pipeline：提取 → LLM 分析 → 分块 → Embedding → 写入存储。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from src.db.models import Chunk, Document
from src.db.neo4j_client import Neo4jClient, EntityData, RelationData
from src.db.postgres import async_session_factory
from src.pipeline.analyzer import analyzer
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
    ) -> None:
        """处理新上传的文件：提取 → 分析 → 分块 → embedding → 写入。

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

                # 4. LLM 分析（overview + 实体 + 关系）
                analysis = await analyzer.analyze(raw_text, title)
                logger.info(
                    f"文档 {doc_id} 分析完成: "
                    f"{len(analysis.entities)} 实体, {len(analysis.relations)} 关系"
                )

                # 5. 文本分块
                chunks = chunk_text(raw_text)
                logger.info(f"文档 {doc_id} 分块完成: {len(chunks)} chunks")

                # 6. Embedding
                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

                # 7. 写入 Postgres
                # 先删除旧 chunks
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
                        overview=analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                # 更新 document
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(
                        raw_text=raw_text,
                        overview=analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        error_msg=None,
                    )
                )
                await session.commit()
                logger.info(f"文档 {doc_id} Postgres 写入完成")

                # 8. 写入 Neo4j
                await self._write_graph(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=file_type,
                    overview=analysis.overview,
                    entities=analysis.entities,
                    relations=analysis.relations,
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

    async def reindex_document(
        self, doc_id: UUID, new_text: str
    ) -> None:
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
                analysis = await analyzer.analyze(new_text, title)
                chunks = chunk_text(new_text)

                if chunks:
                    texts = [c.text for c in chunks]
                    embeddings = await embedder.embed_batch(texts)
                else:
                    embeddings = []

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
                        overview=analysis.overview,
                        doc_uri=doc_uri,
                        token_count=chunk.token_count,
                    )
                    session.add(db_chunk)

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(
                        raw_text=new_text,
                        overview=analysis.overview,
                        content_hash=content_hash,
                        status="indexed",
                        error_msg=None,
                    )
                )
                await session.commit()

                # 更新 Neo4j
                await self._neo4j.upsert_document_node(
                    doc_id=str(doc_id),
                    title=title,
                    file_type=doc.file_type,
                    overview=analysis.overview,
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

    async def _write_graph(
        self,
        doc_id: str,
        title: str,
        file_type: str,
        overview: str,
        entities: list,
        relations: list,
    ) -> None:
        """将实体和关系写入 Neo4j。"""
        # Document 节点
        await self._neo4j.upsert_document_node(
            doc_id=doc_id, title=title, file_type=file_type, overview=overview
        )

        # 实体节点
        for entity in entities:
            await self._neo4j.upsert_entity(
                doc_id=doc_id,
                entity=EntityData(
                    name=entity.name,
                    entity_type=entity.type,
                    description=entity.description,
                ),
            )

        # 关系
        for relation in relations:
            await self._neo4j.create_relation(
                RelationData(
                    from_name=relation.from_name,
                    to_name=relation.to_name,
                    relation_type=relation.type,
                    description=relation.description,
                )
            )
