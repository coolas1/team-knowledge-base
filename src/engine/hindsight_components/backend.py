"""KnowledgeBase-compatible adapter for the TKB Hindsight implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.engine.interface import (
    Capabilities,
    DocumentRef,
    GraphData,
    GraphNode,
    IngestSource,
    RecallRequest,
    RecallResult,
)

from .contracts import (
    DocumentRepository,
    HindsightMemory,
    SourceExtractor,
    StoredDocument,
)
from .extractor import ProjectSourceExtractor
from .mapper import map_entity_graph, map_recall


def _to_ref(document: StoredDocument) -> DocumentRef:
    return DocumentRef(
        id=document.id,
        title=document.title,
        file_type=document.file_type,
        status=document.status,
        overview=document.overview,
        error_msg=document.error_msg,
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


class HindsightBackend:
    """Adapt the existing TKB memory engine to the stable engine contract.

    The adapter is deliberately constructed with injected persistence and
    memory ports.  It therefore coexists with GraphRAG and does not import the
    source repository's global database session or duplicate ``Document`` ORM.
    """

    capabilities = Capabilities(
        graph=True,
        partial_update=True,
        multimodal=True,
        namespace=False,
    )

    def __init__(
        self,
        memory: HindsightMemory,
        documents: DocumentRepository,
        *,
        extractor: SourceExtractor | None = None,
        recall_mode: str = "deep",
    ) -> None:
        if recall_mode not in {"fast", "deep"}:
            raise ValueError(f"unsupported retrieval mode: {recall_mode}")
        self._memory = memory
        self._documents = documents
        self._extractor = extractor or ProjectSourceExtractor()
        self._recall_mode = recall_mode

    async def ingest(self, source: IngestSource) -> DocumentRef:
        raw_text, file_type = await self._extractor.extract(
            source.name,
            source.data,
            str(source.path) if source.path is not None else None,
        )
        if not raw_text.strip():
            raise ValueError("文档提取结果为空")

        document = await self._documents.create(
            title=source.name,
            file_type=file_type,
            raw_text=raw_text,
        )
        document = await self._documents.set_status(document.id, "processing")
        try:
            await self._retain(document)
        except Exception as exc:
            return _to_ref(
                await self._documents.set_status(
                    document.id, "failed", error_msg=str(exc)
                )
            )
        return _to_ref(await self._documents.set_status(document.id, "indexed"))

    async def reingest(self, doc_id: str) -> DocumentRef:
        document = await self._documents.get(doc_id)
        if document is None:
            raise ValueError(f"文档不存在: {doc_id}")
        document = await self._documents.set_status(doc_id, "processing")
        try:
            await self._retain(document)
        except Exception as exc:
            return _to_ref(
                await self._documents.set_status(doc_id, "failed", error_msg=str(exc))
            )
        return _to_ref(await self._documents.set_status(doc_id, "indexed"))

    async def _retain(self, document: StoredDocument) -> None:
        await self._memory.retain_document(
            document_id=document.id,
            title=document.title,
            content=document.raw_text,
            file_type=document.file_type,
            source_type="upload",
        )

    async def remove(self, doc_id: str) -> None:
        document = await self._documents.get(doc_id)
        if document is None:
            return
        await self._memory.delete_document(doc_id, missing_ok=True)
        await self._documents.delete(doc_id)

    async def recall(self, request: RecallRequest) -> RecallResult:
        if request.top_k < 1:
            raise ValueError("top_k 必须大于 0")
        payload = await self._memory.recall(request.query, mode=self._recall_mode)
        return map_recall(payload, top_k=request.top_k)

    async def get_graph(self, entity: str | None = None) -> GraphData:
        if entity is None:
            return map_entity_graph(await self._memory.entity_graph())
        details = await self._memory.get_entity_by_name(entity)
        if not details:
            return GraphData()
        observations = details.get("observations", [])
        sources = [dict(item) for item in observations if isinstance(item, Mapping)]
        return GraphData(
            nodes=[
                GraphNode(
                    name=str(details.get("canonical_name") or entity),
                    type="memory_entity",
                    description=str(details.get("metadata") or ""),
                    sources=sources,
                )
            ]
        )

    async def get_neighbors(self, entity: str) -> GraphData:
        graph = await self.get_graph()
        names = {entity}
        links = []
        for link in graph.links:
            if link.source == entity or link.target == entity:
                links.append(link)
                names.add(link.source)
                names.add(link.target)
        return GraphData(
            nodes=[node for node in graph.nodes if node.name in names],
            links=links,
        )

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 20,
        file_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if page < 1 or page_size < 1:
            raise ValueError("page 和 page_size 必须大于 0")
        total, documents = await self._documents.list(
            page=page,
            page_size=page_size,
            file_type=file_type,
            status=status,
        )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": item.id,
                    "title": item.title,
                    "file_type": item.file_type,
                    "status": item.status,
                    "overview": item.overview[:200],
                    "created_at": _iso(item.created_at),
                    "updated_at": _iso(item.updated_at),
                }
                for item in documents
            ],
        }

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        document = await self._documents.get(doc_id)
        if document is None:
            return None
        return {
            "id": document.id,
            "title": document.title,
            "file_type": document.file_type,
            "raw_text": document.raw_text,
            "overview": document.overview,
            "status": document.status,
            "error_msg": document.error_msg,
            "created_at": _iso(document.created_at),
            "updated_at": _iso(document.updated_at),
        }
