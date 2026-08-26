"""GraphRAG backend: implements the KnowledgeBase contract.

Migrated from src/core/knowledge_base.py + src/core/search.py. The backend
owns its own DB sessions (async_session_factory); callers never pass a session.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload  # noqa: F401  (kept for parity with original)

from src.engine.components.analyzer import Analyzer
from src.engine.components.extractors.registry import ExtractorRegistry, registry
from src.engine.components.store.models import Chunk, Document
from src.engine.components.store.neo4j import Neo4jClient
from src.engine.components.store.postgres import async_session_factory, init_db
from src.engine.config import EngineConfig
from src.engine.graphrag.pipeline import Pipeline
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

UPLOAD_DIR = Path("uploads")


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


def _to_ref(doc: Document, chunk_count: int = 0, overview: str | None = None) -> DocumentRef:
    return DocumentRef(
        id=str(doc.id),
        title=doc.title,
        file_type=doc.file_type,
        status=doc.status,
        overview=overview if overview is not None else (doc.overview or ""),
        error_msg=doc.error_msg,
    )


class GraphRAGBackend:
    """GraphRAG implementation of KnowledgeBase."""

    capabilities = Capabilities(graph=True, partial_update=True, multimodal=True)

    def __init__(self, neo4j: Neo4jClient, pipeline: Pipeline) -> None:
        self._neo4j = neo4j
        self._pipeline = pipeline

    # ── ingest / reingest / remove ───────────────────────────────

    async def ingest(self, source: IngestSource) -> DocumentRef:
        data = source.data
        if source.path is not None and not data:
            data = source.path.read_bytes()
        file_type = ExtractorRegistry.guess_file_type(Path(source.name))

        doc_id = uuid.uuid4()
        doc_dir = UPLOAD_DIR / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        file_path = doc_dir / source.name
        file_path.write_bytes(data)

        async with async_session_factory() as session:
            doc = Document(
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

        asyncio.create_task(
            self._pipeline.process_file(doc_id, file_path, source.name, file_type)
        )
        return ref

    async def edit_content(self, doc_id: str, content: str) -> DocumentRef:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                raise ValueError(f"文档不存在: {doc_id}")
            await session.execute(
                update(Document)
                .where(Document.id == uid)
                .values(raw_text=content, status="pending", error_msg=None)
            )
            await session.commit()
            await session.refresh(doc)
            ref = _to_ref(doc)

        asyncio.create_task(self._pipeline.reindex_document(uid, content))
        return ref

    async def reingest(self, doc_id: str) -> DocumentRef:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
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
            asyncio.create_task(self._pipeline.reindex_document(uid, new_text))
        else:
            assert file_path is not None
            asyncio.create_task(
                self._pipeline.process_file(uid, file_path, title, file_type)
            )
        return ref

    async def remove(self, doc_id: str) -> None:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                return
            await self._pipeline.before_remove(doc_id)
            _remove_upload_directory(uid)
            await session.delete(doc)
            await session.commit()
        await self._neo4j.delete_document_graph(doc_id)

    # ── recall ───────────────────────────────────────────────────

    async def recall(self, request: RecallRequest) -> RecallResult:
        from src.engine.graphrag._search import full_search

        async with async_session_factory() as session:
            result = await full_search(
                session, self._neo4j, request.query, top_k=request.top_k
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
                nodes=[GraphNode(
                    name=details.name, type=details.entity_type,
                    description=details.properties.get("description", ""),
                    sources=details.properties.get("sources", [])
                            if isinstance(details.properties.get("sources"), list) else [],
                )],
                links=[GraphLink(source=r.get("other_name", ""), target=details.name,
                                 type=r.get("type", ""), description=r.get("description", ""))
                       for r in details.relations if r.get("type")],
            )
        return GraphData(
            nodes=[GraphNode(name=n["name"], type=n["type"], description=n.get("description", ""),
                             sources=n.get("sources", [])) for n in raw.get("nodes", [])],
            links=[GraphLink(source=l["source"], target=l["target"], type=l["type"],
                             description=l.get("description", "")) for l in raw.get("links", [])],
        )

    async def get_neighbors(self, entity: str) -> GraphData:
        results = await self._neo4j.query_neighbors(entity, hops=2)
        return GraphData(
            nodes=[GraphNode(name=r.name, type=r.entity_type,
                             description=r.properties.get("description", "")) for r in results],
            links=[],
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
            stmt = select(Document).order_by(Document.created_at.desc())
            if file_type:
                stmt = stmt.where(Document.file_type == file_type)
            if status:
                stmt = stmt.where(Document.status == status)

            count_stmt = select(func.count(Document.id))
            if file_type:
                count_stmt = count_stmt.where(Document.file_type == file_type)
            if status:
                count_stmt = count_stmt.where(Document.status == status)
            total = (await session.execute(count_stmt)).scalar() or 0

            stmt = stmt.offset((page - 1) * page_size).limit(page_size)
            docs = (await session.execute(stmt)).scalars().all()

            return {
                "total": total, "page": page, "page_size": page_size,
                "items": [
                    {
                        "id": str(d.id), "title": d.title, "file_type": d.file_type,
                        "status": d.status,
                        "overview": (d.overview or "")[:200],
                        "created_at": d.created_at.isoformat() if d.created_at else None,
                        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
                    }
                    for d in docs
                ],
            }

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        uid = uuid.UUID(doc_id)
        async with async_session_factory() as session:
            doc = await session.get(Document, uid)
            if not doc:
                return None
            count_stmt = select(func.count(Chunk.id)).where(Chunk.doc_id == uid)
            chunk_count = (await session.execute(count_stmt)).scalar() or 0
            return {
                "id": str(doc.id), "title": doc.title, "file_type": doc.file_type,
                "raw_text": doc.raw_text, "overview": doc.overview,
                "file_path": doc.file_path, "content_hash": doc.content_hash,
                "status": doc.status, "error_msg": doc.error_msg,
                "chunk_count": chunk_count,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            }


def build(config: EngineConfig) -> GraphRAGBackend:
    """Factory used by src.engine.config.build_engine."""
    from src.engine.components.analyzer import Analyzer

    neo4j = Neo4jClient()
    analyzer = Analyzer(schema_path=config.config_dir / "entity_schema.yaml")
    pipeline = Pipeline(neo4j, analyzer=analyzer, index_hook=config.index_hook)
    return GraphRAGBackend(neo4j, pipeline)
