"""Thin MCP adapter: exposes a KnowledgeBase instance as MCP tools over
streamable HTTP. No business logic - each tool wraps one KnowledgeBase method.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from src.engine.interface import KnowledgeBase

mcp = FastMCP("Team Knowledge Base")

_kb: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


async def search(query: str) -> dict[str, Any]:
    """语义检索知识库（向量粗筛 -> Reranker 守门 -> 图谱增强）。"""
    from src.engine.interface import RecallRequest

    result = await _get_kb().recall(RecallRequest(query=query))
    return {
        "chunks": [
            {
                "doc_id": c.doc_id, "title": c.title,
                "chunk_text": c.chunk_text[:1000],
                "reranker_score": c.reranker_score, "vector_score": c.vector_score,
            }
            for c in result.chunks
        ],
        "related_entities": result.related_entities,
        "related_docs": result.related_docs,
    }


async def get_document(doc_id: str) -> dict[str, Any]:
    """获取文件详情。"""
    result = await _get_kb().get_document(doc_id)
    if not result:
        return {"error": f"文档不存在: {doc_id}"}
    return result


async def query_graph(
    entity_name: str, include_neighbors: bool = True, hops: int = 2
) -> dict[str, Any]:
    """查询知识图谱中的实体及其关系。"""
    kb = _get_kb()
    graph = await kb.get_graph(entity_name)
    if not graph.nodes:
        return {"error": f"实体不存在: {entity_name}"}
    node = graph.nodes[0]
    out: dict[str, Any] = {
        "name": node.name, "type": node.type,
        "properties": {"description": node.description, "sources": node.sources},
        "relations": [
            {"type": l.type, "other": l.target if l.source == node.name else l.source,
             "description": l.description}
            for l in graph.links
        ],
    }
    if include_neighbors:
        neighbors = await kb.get_neighbors(entity_name)
        out["neighbors"] = [
            {"name": n.name, "type": n.type, "description": n.description}
            for n in neighbors.nodes
        ]
    return out


async def upload_document(file_name: str, content: str) -> dict[str, Any]:
    """上传文档到知识库（文本内容直接上传）。"""
    from src.engine.interface import IngestSource

    ref = await _get_kb().ingest(
        IngestSource(name=file_name, data=content.encode("utf-8"))
    )
    return {
        "id": ref.id, "title": ref.title, "file_type": ref.file_type, "status": ref.status,
    }


# Register the async functions as MCP tools (FastMCP introspects signatures).
mcp.tool()(search)
mcp.tool()(get_document)
mcp.tool()(query_graph)
mcp.tool()(upload_document)


def build_app():
    """Return the streamable-HTTP ASGI app (no lifespan; caller manages sessions)."""
    return mcp.streamable_http_app()
