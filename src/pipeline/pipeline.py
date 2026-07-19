"""文件入库 Pipeline：提取 → 分块 → LLM 分析 → Embedding → 写入存储。"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from src.core.bm25_index import bm25_index, BM25Entry
from src.core.log_manager import pipeline_trace
from src.db.models import Chunk, Document, DocumentVersion
from src.db.neo4j_client import Neo4jClient, EntityData, EntitySource, RelationData
from src.db.postgres import async_session_factory
from src.pipeline.analyzer import analyzer, ChunkAnalysisResult
from src.pipeline.chunker import chunk_text
from src.pipeline.embedder import embedder
from src.pipeline.extractors.registry import registry

logger = logging.getLogger(__name__)


def _sanitize_text(text: str) -> str:
    """清理文本中的非法字符。

    pypdf 等提取器可能产生以下问题字符：
    - null 字节（\\x00）：PostgreSQL TEXT 列不允许
    - 未配对的 Unicode 代理字符：UTF-8 编码时报 surrogates not allowed
    """
    text = text.replace("\x00", "")
    return text.encode("utf-8", errors="replace").decode("utf-8")


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
        """处理新上传的文件：提取 → 分块 → 分析 → embedding → 写入。

        幂等性：通过 content_hash (SHA256) 判断，内容未变则跳过。
        """
        with pipeline_trace() as trace_id:
            total_start = time.monotonic()
            logger.info(f"[trace={trace_id}] Pipeline 开始: 文档 {doc_id} | 类型={file_type} | 标题={title}")

            async with async_session_factory() as session:
                # 1. 读取文件并计算 hash
                raw_bytes = file_path.read_bytes()
                content_hash = hashlib.sha256(raw_bytes).hexdigest()
                file_size = len(raw_bytes)

                # 检查幂等性
                doc = await session.get(Document, doc_id)
                if doc and doc.content_hash == content_hash and doc.index_status == "indexed":
                    logger.info(f"[trace={trace_id}] 文档 {doc_id} 内容未变，跳过 pipeline")
                    return

                # 2. 标记为 processing
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(index_status="processing", error_msg=None)
                )
                await session.commit()

                try:
                    # 3. 文本提取
                    t0 = time.monotonic()
                    raw_text = _sanitize_text(registry.extract(file_path))
                    extract_ms = (time.monotonic() - t0) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} 文本提取完成: "
                        f"{len(raw_text)} 字符 | 文件 {file_size} 字节 | 耗时 {extract_ms:.0f}ms"
                    )
                    
                    # 3.5 版本快照：保存原始文件副本 + 更新版本记录
                    await self._save_version_snapshot(
                        session, doc_id, file_path, raw_text, content_hash, trace_id
                    )

                    # 4. 文本分块
                    t0 = time.monotonic()
                    chunks = chunk_text(raw_text)
                    chunk_ms = (time.monotonic() - t0) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} 分块完成: "
                        f"{len(chunks)} chunks | 耗时 {chunk_ms:.0f}ms"
                    )

                    # 5. 文档级 overview + file_relations
                    t0 = time.monotonic()
                    doc_analysis = await analyzer.analyze_overview(raw_text, title)
                    overview_ms = (time.monotonic() - t0) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} overview 生成完成: "
                        f"{len(doc_analysis.file_relations)} file_relations | 耗时 {overview_ms:.0f}ms"
                    )

                    # 6. 逐 Chunk LLM 分析
                    t0 = time.monotonic()
                    chunk_analyses: list[ChunkAnalysisResult] = []
                    for chunk in chunks:
                        ct = time.monotonic()
                        ca = await analyzer.analyze_chunk(
                            chunk.text, title, chunk.index
                        )
                        chunk_analyses.append(ca)
                        ck_ms = (time.monotonic() - ct) * 1000
                        logger.info(
                            f"[trace={trace_id}] 文档 {doc_id} chunk[{chunk.index}]: "
                            f"{len(ca.entities)} 实体, {len(ca.relations)} 关系 | 耗时 {ck_ms:.0f}ms"
                        )
                    analysis_ms = (time.monotonic() - t0) * 1000
                    total_entities = sum(len(ca.entities) for ca in chunk_analyses)
                    total_relations = sum(len(ca.relations) for ca in chunk_analyses)
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} LLM chunk 分析完成: "
                        f"共 {total_entities} 实体, {total_relations} 关系 | 总耗时 {analysis_ms:.0f}ms"
                    )

                    # 7. Embedding
                    t0 = time.monotonic()
                    if chunks:
                        texts = [c.text for c in chunks]
                        embeddings = await embedder.embed_batch(texts)
                    else:
                        embeddings = []
                    embed_ms = (time.monotonic() - t0) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} Embedding 完成: "
                        f"{len(embeddings)} 向量 | 耗时 {embed_ms:.0f}ms"
                    )

                    # 8. 写入 Postgres
                    t0 = time.monotonic()
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
                            index_status="indexed",
                            error_msg=None,
                        )
                    )
                    await session.commit()
                    pg_ms = (time.monotonic() - t0) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} Postgres 写入完成: "
                        f"{len(chunks)} chunks | 耗时 {pg_ms:.0f}ms"
                    )

                    # 9. 写入 Neo4j
                    t0 = time.monotonic()
                    await self._write_graph(
                        doc_id=str(doc_id),
                        title=title,
                        file_type=file_type,
                        overview=doc_analysis.overview,
                        chunk_analyses=chunk_analyses,
                        file_relations=doc_analysis.file_relations,
                        session=session,
                        trace_id=trace_id,
                    )
                    neo4j_ms = (time.monotonic() - t0) * 1000
                    total_ms = (time.monotonic() - total_start) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} Pipeline 完成 ✓ "
                        f"Neo4j {neo4j_ms:.0f}ms | 全链路 {total_ms:.0f}ms "
                        f"[提取 {extract_ms:.0f} + 分块 {chunk_ms:.0f} + "
                        f"LLM {analysis_ms:.0f} + Embed {embed_ms:.0f} + "
                        f"PG {pg_ms:.0f} + Neo4j {neo4j_ms:.0f}]"
                    )

                    # 同步 BM25 索引
                    self._sync_bm25_index(str(doc_id), chunks, doc_analysis.overview, doc_uri)

                except Exception as e:
                    total_ms = (time.monotonic() - total_start) * 1000
                    logger.error(
                        f"[trace={trace_id}] 文档 {doc_id} Pipeline 失败: {e} "
                        f"(已运行 {total_ms:.0f}ms)", exc_info=True
                    )
                    await session.rollback()
                    await session.execute(
                        update(Document)
                        .where(Document.id == doc_id)
                        .values(index_status="failed", error_msg=str(e)[:500])
                    )
                    await session.commit()

    async def reindex_document(
        self, doc_id: UUID, new_text: str
    ) -> None:
        """编辑后重新索引：跳过文本提取，直接从文本开始分析。"""
        with pipeline_trace() as trace_id:
            total_start = time.monotonic()
            logger.info(f"[trace={trace_id}] Re-index 开始: 文档 {doc_id}")

            async with async_session_factory() as session:
                doc = await session.get(Document, doc_id)
                if not doc:
                    raise ValueError(f"文档不存在: {doc_id}")

                title = doc.title
                new_text = _sanitize_text(new_text)
                content_hash = hashlib.sha256(new_text.encode()).hexdigest()

                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(index_status="processing", error_msg=None)
                )
                await session.commit()

                try:
                    t0 = time.monotonic()
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
                    analysis_ms = (time.monotonic() - t0) * 1000

                    await session.execute(
                        Chunk.__table__.delete().where(Chunk.doc_id == doc_id)  # type: ignore[union-attr]
                    )

                    doc_analysis = await analyzer.analyze_overview(new_text, title)

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
                            index_status="indexed",
                            error_msg=None,
                        )
                    )
                    await session.commit()

                    # 更新 Neo4j
                    await self._neo4j.delete_document_graph(str(doc_id))
                    await self._neo4j.upsert_document_node(
                        doc_id=str(doc_id),
                        title=title,
                        file_type=doc.file_type,
                        overview=doc_analysis.overview,
                    )
                    for ca in chunk_analyses:
                        source = EntitySource(
                            doc_id=str(doc_id), chunk_index=ca.chunk_index, doc_title=title
                        )
                        for entity in ca.entities:
                            await self._neo4j.upsert_entity(
                                entity=EntityData(name=entity.name, entity_type=entity.type, description=entity.description),
                                source=source,
                            )
                        for relation in ca.relations:
                            await self._neo4j.upsert_relation(
                                relation=RelationData(from_name=relation.from_name, to_name=relation.to_name, relation_type=relation.type, description=relation.description),
                                source=source,
                            )

                    if doc_analysis.file_relations:
                        await self._write_file_relations(
                            str(doc_id), doc_analysis.file_relations, session, trace_id
                        )

                    total_ms = (time.monotonic() - total_start) * 1000
                    logger.info(
                        f"[trace={trace_id}] 文档 {doc_id} re-index 完成 ✓ "
                        f"{len(chunks)} chunks, {len(chunk_analyses)} 分析 | 总耗时 {total_ms:.0f}ms"
                    )

                    # 同步 BM25 索引
                    self._sync_bm25_index(str(doc_id), chunks, doc_analysis.overview, doc_uri)

                except Exception as e:
                    total_ms = (time.monotonic() - total_start) * 1000
                    logger.error(
                        f"[trace={trace_id}] 文档 {doc_id} re-index 失败: {e} "
                        f"(已运行 {total_ms:.0f}ms)", exc_info=True
                    )
                    await session.rollback()
                    await session.execute(
                        update(Document)
                        .where(Document.id == doc_id)
                        .values(index_status="failed", error_msg=str(e)[:500])
                    )
                    await session.commit()

    async def _write_graph(
        self,
        doc_id: str,
        title: str,
        file_type: str,
        overview: str,
        chunk_analyses: list[ChunkAnalysisResult],
        file_relations: list,
        session,
        trace_id: str,
    ) -> None:
        """三层图谱写入：L1 chunk 级 + L2 文档内聚合 + L3 跨文档关联。"""
        # Document 节点
        await self._neo4j.upsert_document_node(
            doc_id=doc_id, title=title, file_type=file_type, overview=overview
        )

        # L1+L2: 逐 chunk 写入实体和关系
        entity_count = 0
        relation_count = 0
        for ca in chunk_analyses:
            source = EntitySource(
                doc_id=doc_id, chunk_index=ca.chunk_index, doc_title=title
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
                entity_count += 1

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
                relation_count += 1

        logger.info(
            f"[trace={trace_id}] Neo4j L1+L2 写入: {entity_count} 实体, {relation_count} 关系"
        )

        # L3: file_relations → Document↔Document 边
        if file_relations:
            await self._write_file_relations(doc_id, file_relations, session, trace_id)

    async def _write_file_relations(
        self, doc_id: str, file_relations: list, session, trace_id: str
    ) -> None:
        """解析 file_relations 并写入 Document↔Document 边。"""
        written = 0
        for fr in file_relations:
            target_title = fr.related_doc_title
            if not target_title:
                continue

            result = await session.execute(
                select(Document.id).where(Document.title == target_title).limit(1)
            )
            target_doc = result.scalar_one_or_none()

            if target_doc is None:
                logger.info(
                    f"[trace={trace_id}] file_relation 目标不存在: {target_title}, 跳过"
                )
                continue

            await self._neo4j.create_doc_relation(
                source_doc_id=doc_id,
                target_doc_id=str(target_doc),
                relation_type=fr.type,
                reason=fr.reason,
            )
            written += 1
            logger.info(
                f"[trace={trace_id}] L3 file_relation: {doc_id} → {target_doc} ({fr.type})"
            )

        logger.info(
            f"[trace={trace_id}] Neo4j L3 写入完成: {written} 条 file_relations"
        )

    async def _save_version_snapshot(
        self,
        session,
        doc_id: UUID,
        file_path: Path,
        raw_text: str,
        content_hash: str,
        trace_id: str,
    ) -> None:
        """保存版本快照：复制原始文件到版本目录 + 更新版本记录的 raw_text。

        如果版本记录已存在（由 FileWatcher 创建），则更新它；
        否则创建新的版本记录。
        """
        import shutil
        from sqlalchemy import func

        t0 = time.monotonic()
        logger.info(
            f"[trace={trace_id}] 版本快照开始: 文档 {doc_id} | "
            f"raw_text={len(raw_text)}字符 | hash={content_hash[:12]}"
        )

        # 确保版本目录存在
        versions_dir = file_path.parent / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)

        # 获取当前最大版本号
        result = await session.execute(
            select(func.max(DocumentVersion.version)).where(DocumentVersion.doc_id == doc_id)
        )
        max_version = result.scalar() or 0

        # 查找是否已有此 content_hash 的版本记录（FileWatcher 可能已创建）
        result = await session.execute(
            select(DocumentVersion).where(
                DocumentVersion.doc_id == doc_id,
                DocumentVersion.content_hash == content_hash,
            ).order_by(DocumentVersion.version.desc()).limit(1)
        )
        existing_version = result.scalar_one_or_none()

        if existing_version and existing_version.version == max_version:
            # FileWatcher 已创建版本记录，更新 raw_text 和快照路径
            snapshot_name = f"v{existing_version.version}_{file_path.name}"
            snapshot_path = versions_dir / snapshot_name
            if not snapshot_path.exists():
                shutil.copy2(str(file_path), str(snapshot_path))

            existing_version.raw_text = raw_text
            existing_version.file_path = str(snapshot_path)
            snap_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"[trace={trace_id}] 更新版本 v{existing_version.version} 快照: "
                f"{snapshot_path.name} | raw_text={len(raw_text)}字符 | 耗时 {snap_ms:.0f}ms"
            )
        else:
            # 创建新版本记录
            new_version = max_version + 1
            snapshot_name = f"v{new_version}_{file_path.name}"
            snapshot_path = versions_dir / snapshot_name
            shutil.copy2(str(file_path), str(snapshot_path))

            version = DocumentVersion(
                doc_id=doc_id,
                version=new_version,
                raw_text=raw_text,
                content_hash=content_hash,
                file_path=str(snapshot_path),
                change_type="modify" if max_version > 0 else "create",
            )
            session.add(version)
            snap_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"[trace={trace_id}] 创建版本 v{new_version} 快照: "
                f"{snapshot_path.name} | raw_text={len(raw_text)}字符 | 耗时 {snap_ms:.0f}ms"
            )

    # ── BM25 索引同步 ─────────────────────────────────────────

    @staticmethod
    def _sync_bm25_index(
        doc_id: str,
        chunks: list,
        overview: str,
        doc_uri: str,
    ) -> None:
        """同步 BM25 索引：移除旧条目，添加新 chunks。"""
        if not bm25_index.ready:
            return
        try:
            # 移除该文档的旧 chunks
            removed = bm25_index.remove_by_doc_id(doc_id)
            # 添加新 chunks
            for chunk in chunks:
                bm25_index.add_entry(BM25Entry(
                    chunk_id="",  # BM25 索引中不需要精确 chunk_id
                    doc_id=doc_id,
                    chunk_index=chunk.index,
                    chunk_text=chunk.text,
                    overview=overview,
                    doc_uri=doc_uri,
                ))
            logger.info(
                f"BM25 索引同步: doc={doc_id} | "
                f"移除 {removed} + 添加 {len(chunks)} chunks"
            )
        except Exception as e:
            logger.warning(f"BM25 索引同步失败: {type(e).__name__}: {e}")
