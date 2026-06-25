"""MCP Server：为 Agent 提供知识库工具接口（streamable HTTP 传输）。"""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.core.knowledge_base import KnowledgeBase
from src.db.neo4j_client import Neo4jClient
from src.db.postgres import async_session_factory

# FastMCP 实例
mcp = FastMCP("Team Knowledge Base")

# KB 实例引用（在 main.py lifespan 中设置）
_kb: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


# ── Tools ────────────────────────────────────────────────────────


@mcp.tool()
async def search(query: str) -> dict[str, Any]:
    """语义检索知识库（三层漏斗：向量粗筛 → overview 守门 → 图谱增强）。

    Args:
        query: 搜索查询文本
    """
    kb = _get_kb()
    async with async_session_factory() as session:
        result = await kb.search(session, query)
        return {
            "answer": result.answer,
            "sources": [
                {
                    "doc_id": s.doc_id,
                    "title": s.title,
                    "chunk_text": s.chunk_text[:500],  # 截断避免过长
                    "score": s.score,
                }
                for s in result.sources
            ],
            "related_entities": result.related_entities,
        }


@mcp.tool()
async def get_document(doc_id: str) -> dict[str, Any]:
    """获取文件详情（含 overview、状态、chunk 数量）。

    Args:
        doc_id: 文件 UUID
    """
    kb = _get_kb()
    async with async_session_factory() as session:
        result = await kb.get_document(session, uuid.UUID(doc_id))
        if not result:
            return {"error": f"文档不存在: {doc_id}"}
        return result


@mcp.tool()
async def query_graph(
    entity_name: str,
    include_neighbors: bool = True,
    hops: int = 2,
) -> dict[str, Any]:
    """查询知识图谱中的实体及其关系。

    Args:
        entity_name: 实体名称
        include_neighbors: 是否包含邻居实体
        hops: 邻居跳数（1-3）
    """
    kb = _get_kb()
    entity = await kb.get_entity(entity_name)
    if not entity:
        return {"error": f"实体不存在: {entity_name}"}

    if include_neighbors:
        neighbors = await kb.get_neighbors(entity_name, hops)
        entity["neighbors"] = neighbors

    return entity


@mcp.tool()
async def upload_document(
    file_name: str,
    content: str,
) -> dict[str, Any]:
    """上传文档到知识库（支持 markdown 文本直接上传）。

    Args:
        file_name: 文件名（含扩展名，如 report.md）
        content: 文件文本内容
    """
    kb = _get_kb()
    async with async_session_factory() as session:
        return await kb.upload_file(session, file_name, content.encode("utf-8"))
