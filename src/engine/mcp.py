"""Thin MCP adapter: exposes a KnowledgeBase instance as MCP tools over
streamable HTTP. No business logic - each tool wraps one KnowledgeBase method.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from src.engine.interface import KnowledgeBase, KnowledgeQuery, KnowledgeQueryRequest

mcp = FastMCP(
    "Team Knowledge Base",
    streamable_http_path="/",
)

_kb: KnowledgeBase | None = None
_query_service: KnowledgeQuery | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def set_query_service(query_service: KnowledgeQuery | None) -> None:
    global _query_service
    _query_service = query_service


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


def _get_query_service() -> KnowledgeQuery:
    if _query_service is None:
        raise RuntimeError("Hindsight 查询服务未初始化")
    return _query_service


async def search(
    query: str,
    top_k: int = 20,
    mode: Literal["auto", "fast", "deep"] = "auto",
    needs_answer: bool = False,
) -> dict[str, Any]:
    """检索知识库。

    Hindsight 启用时使用其 recall/reflect：简单事实选择 fast，复杂比较、
    多跳和时间线选择 deep；需要服务端生成答案时设置 needs_answer=true。
    旧调用只传 query/top_k 仍兼容。Hindsight 未启用时回退到 GraphRAG。
    """
    from src.engine.interface import RecallRequest

    request = RecallRequest(
        query=query,
        top_k=top_k,
        mode=mode,
        needs_answer=needs_answer,
    )
    if _query_service is not None:
        from src.engine.hindsight_components.compat import HindsightRecallAdapter

        result = await HindsightRecallAdapter(_query_service).recall(request)
    else:
        if mode != "auto" or needs_answer:
            raise RuntimeError("Hindsight 查询服务未初始化")
        result = await _get_kb().recall(request)

    payload = asdict(result)
    for chunk in payload["chunks"]:
        chunk["chunk_text"] = chunk["chunk_text"][:1000]
    return payload


async def query_knowledge(
    query: str,
    strategy: Literal["auto", "recall", "reflect"] = "auto",
    mode: Literal["fast", "deep"] = "deep",
    top_k: int = 10,
    needs_answer: bool = True,
) -> dict[str, Any]:
    """通过 Hindsight 统一入口执行 recall 或 reflect。"""
    result = await _get_query_service().query(
        KnowledgeQueryRequest(
            query=query,
            strategy=strategy,
            mode=mode,
            top_k=top_k,
            needs_answer=needs_answer,
        )
    )
    return asdict(result)


async def search_knowledge_fast(
    query: str,
    top_k: int = 5,
) -> dict[str, Any]:
    """快速知识检索。用于简单事实、定义、明确关键词、指定文件内容和文件定位。

    只返回检索证据，不在服务端生成最终答案；调用此工具的模型应根据 sources
    组织回答。不要用于跨文档比较、多跳关系、时间线或复杂综合分析。
    """
    return await query_knowledge(
        query,
        strategy="recall",
        mode="fast",
        top_k=top_k,
        needs_answer=False,
    )


async def search_knowledge_deep(
    query: str,
    top_k: int = 10,
) -> dict[str, Any]:
    """深度知识检索。用于跨文档比较、多跳关系、时间线、原因分析和综合总结。

    只返回检索证据，不在服务端生成最终答案；调用此工具的模型应综合 sources、
    related_entities 和 based_on 回答。简单事实查询应优先使用 search_knowledge_fast。
    """
    return await query_knowledge(
        query,
        strategy="recall",
        mode="deep",
        top_k=top_k,
        needs_answer=False,
    )


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
        "name": node.name,
        "type": node.type,
        "properties": {"description": node.description, "sources": node.sources},
        "relations": [
            {
                "type": link.type,
                "other": link.target if link.source == node.name else link.source,
                "description": link.description,
            }
            for link in graph.links
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
    result = {
        "id": ref.id,
        "title": ref.title,
        "file_type": ref.file_type,
        "status": ref.status,
    }
    if ref.memory_status is not None:
        result.update(
            {
                "memory_status": ref.memory_status,
                "memory_error_msg": ref.memory_error_msg,
                "memory_count": ref.memory_count,
                "memory_link_count": ref.memory_link_count,
            }
        )
    return result


async def list_documents(
    page: int = 1,
    page_size: int = 20,
    file_type: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """文件列表（分页，按 type/status 筛选）。"""
    return await _get_kb().list_documents(page, page_size, file_type, status)


async def remove_document(doc_id: str) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j + 本地文件）。"""
    await _get_kb().remove(doc_id)
    return {"removed": doc_id}


async def get_full_graph() -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系）。"""
    graph = await _get_kb().get_graph(None)
    return {
        "nodes": [
            {
                "name": n.name,
                "type": n.type,
                "description": n.description,
                "sources": n.sources,
            }
            for n in graph.nodes
        ],
        "links": [
            {
                "source": link.source,
                "target": link.target,
                "type": link.type,
                "description": link.description,
            }
            for link in graph.links
        ],
    }


# Register the async functions as MCP tools (FastMCP introspects signatures).
mcp.tool()(search)
mcp.tool()(query_knowledge)
mcp.tool()(search_knowledge_fast)
mcp.tool()(search_knowledge_deep)
mcp.tool()(get_document)
mcp.tool()(query_graph)
mcp.tool()(upload_document)
mcp.tool()(list_documents)
mcp.tool()(remove_document)
mcp.tool()(get_full_graph)


def build_app():
    """Return the streamable-HTTP ASGI app; caller manages MCP sessions."""
    return mcp.streamable_http_app()
