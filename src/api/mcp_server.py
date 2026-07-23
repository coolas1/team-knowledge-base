"""MCP Server：为 Agent 提供知识库工具接口（streamable HTTP 传输）。"""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, or_, select

from src.api.context import (
    RequestPrincipal,
    can_read,
    can_write,
    get_forwarded_ollama_username,
    is_trusted_ollama_source,
    resolve_single_trusted_ollama_principal,
    resolve_token_principal,
    resolve_trusted_ollama_principal,
)
from src.core.knowledge_base import KnowledgeBase
from src.db.config import settings
from src.db.models import Document, Team, TrustedOllamaAccount
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


async def _get_mcp_principal() -> RequestPrincipal:
    """Resolve a bearer token or a trusted, administrator-approved Ollama user."""
    authorization: str | None = None
    ollama_username: str | None = None
    requested_team_id: str | None = None
    source_host: str | None = None
    try:
        request = mcp.get_context().request_context.request
        headers = getattr(request, "headers", None)
        if headers is not None:
            authorization = headers.get("authorization")
            ollama_username = get_forwarded_ollama_username(headers)
            requested_team_id = headers.get("x-tkb-team")
        query_params = getattr(request, "query_params", None)
        if query_params is not None:
            ollama_username = ollama_username or query_params.get("ollama_user")
            requested_team_id = requested_team_id or query_params.get("team_id")
        client = getattr(request, "client", None)
        source_host = getattr(client, "host", None)
    except (LookupError, ValueError):
        # Direct in-process calls have no HTTP request and retain legacy behavior.
        pass

    if authorization:
        scheme, _, request_token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not request_token:
            raise RuntimeError("MCP Authorization 必须使用 Bearer token")
        principal = await resolve_token_principal(request_token)
        if principal is None:
            raise RuntimeError("MCP Bearer token 无效、已停用或已过期")
    elif ollama_username:
        if not is_trusted_ollama_source(source_host):
            raise RuntimeError("Ollama 用户名认证请求不来自受信网络")
        principal = await resolve_trusted_ollama_principal(
            ollama_username, requested_team_id
        )
        if principal is None:
            team_hint = f" / {requested_team_id}" if requested_team_id else ""
            raise RuntimeError(f"Ollama 账号未获授权: {ollama_username}{team_hint}")
    else:
        principal = None
        if is_trusted_ollama_source(source_host):
            principal = await resolve_single_trusted_ollama_principal(requested_team_id)
        if principal is None:
            token = settings.mcp_api_token
            if not token:
                raise RuntimeError(
                    "无法自动确定 Ollama 账号；请在后台只保留一个可信用户名，"
                    "或发送 ollama_user"
                )
            principal = await resolve_token_principal(token)
            if principal is None:
                raise RuntimeError("兼容 MCP_API_TOKEN 无效、已停用或已过期")
    if not can_read(principal):
        raise RuntimeError("当前 MCP 用户没有知识库读取权限")
    return principal


def _require_mcp_write(principal: RequestPrincipal) -> None:
    if not can_write(principal):
        raise RuntimeError("当前 MCP 用户只有只读权限，不能上传文档")


async def _principal_for_knowledge_base(
    principal: RequestPrincipal,
    knowledge_base_id: str | None,
) -> RequestPrincipal:
    """Select one authorized team without trusting a raw team argument."""
    if not knowledge_base_id or knowledge_base_id == principal.team_id:
        return principal
    if (
        principal.auth_source != "ollama-account"
        or knowledge_base_id not in principal.accessible_team_ids
    ):
        raise RuntimeError(f"当前账号无权访问知识库: {knowledge_base_id}")
    selected = await resolve_trusted_ollama_principal(
        principal.subject, knowledge_base_id
    )
    if selected is None or not can_read(selected):
        raise RuntimeError(f"当前账号无权访问知识库: {knowledge_base_id}")
    return selected


# ── Tools ────────────────────────────────────────────────────────


@mcp.tool()
async def get_current_context() -> dict[str, Any]:
    """返回 MCP 当前可信身份、团队和角色。回答团队相关问题前应先调用。"""
    principal = await _get_mcp_principal()
    return {
        "team_id": principal.team_id,
        "subject": principal.subject,
        "roles": list(principal.roles),
        "auth_source": principal.auth_source,
    }


@mcp.tool()
async def list_knowledge_bases() -> dict[str, Any]:
    """列出当前 MCP 身份可访问的团队知识库。"""
    principal = await _get_mcp_principal()
    async with async_session_factory() as session:
        if principal.auth_source == "ollama-account":
            memberships = (
                await session.scalars(
                    select(TrustedOllamaAccount)
                    .where(
                        TrustedOllamaAccount.username == principal.subject,
                        TrustedOllamaAccount.active.is_(True),
                    )
                    .order_by(TrustedOllamaAccount.team_id)
                )
            ).all()
            access = [(membership.team_id, list(membership.roles)) for membership in memberships]
        else:
            access = [(principal.team_id, list(principal.roles))]

        items: list[dict[str, Any]] = []
        for team_id, roles in access:
            team = await session.get(Team, team_id)
            document_count = await session.scalar(
                select(func.count(Document.id)).where(
                    or_(Document.team_id == team_id, Document.scope == "public")
                )
            )
            public_document_count = await session.scalar(
                select(func.count(Document.id)).where(Document.scope == "public")
            )
            items.append({
                "id": team_id,
                "name": team.name if team else team_id,
                "document_count": document_count or 0,
                "public_document_count": public_document_count or 0,
                "roles": roles,
                "selected": team_id == principal.team_id,
            })
    return {
        "current_team_id": principal.team_id,
        "subject": principal.subject,
        "auth_source": principal.auth_source,
        "includes_public_documents": True,
        "items": items,
    }


@mcp.tool()
async def search(
    query: str,
    tags: list[str] | None = None,
    scopes: list[str] | None = None,
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    """语义检索知识库（向量粗筛 → Reranker 守门 → 图谱增强）。

    返回 reranker 过滤后的 chunks 和相关实体，
    由 Agent 整合 query 与知识生成回答。

    Args:
        query: 搜索查询文本
        knowledge_base_id: 可选；留空搜索全部已授权团队及公共文档，指定时仅搜索该团队及公共文档
    """
    kb = _get_kb()
    base_principal = await _get_mcp_principal()
    if knowledge_base_id:
        principal = await _principal_for_knowledge_base(
            base_principal, knowledge_base_id
        )
        searched_team_ids = [principal.team_id]
        search_mode = "selected-team-and-public"
    else:
        principal = base_principal
        searched_team_ids = list(
            principal.accessible_team_ids or (principal.team_id,)
        )
        search_mode = "all-accessible-teams-and-public"
    async with async_session_factory() as session:
        result = await kb.search(
            session,
            query,
            team_id=principal.team_id,
            team_ids=searched_team_ids,
            tags=tags,
            scopes=scopes,
        )
        return {
            "knowledge_base_id": knowledge_base_id,
            "search_mode": search_mode,
            "searched_knowledge_base_ids": searched_team_ids,
            "includes_public_documents": True,
            "chunks": [
                {
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "chunk_text": c.chunk_text[:1000],
                    "reranker_score": c.reranker_score,
                    "vector_score": c.vector_score,
                    "owner_team_id": c.owner_team_id,
                    "scope": c.scope,
                }
                for c in result.chunks
            ],
            "related_entities": result.related_entities,
            "related_docs": result.related_docs,
        }


@mcp.tool()
async def get_document(doc_id: str, knowledge_base_id: str | None = None) -> dict[str, Any]:
    """获取文件详情（含 overview、状态、chunk 数量）。

    Args:
        doc_id: 文件 UUID
        knowledge_base_id: 可选；文档所在的已授权团队知识库 ID
    """
    kb = _get_kb()
    principal = await _principal_for_knowledge_base(
        await _get_mcp_principal(), knowledge_base_id
    )
    async with async_session_factory() as session:
        result = await kb.get_document(session, uuid.UUID(doc_id), principal.team_id)
        if not result:
            return {"error": f"文档不存在: {doc_id}"}
        return result


@mcp.tool()
async def query_graph(
    entity_name: str,
    include_neighbors: bool = True,
    hops: int = 2,
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    """查询知识图谱中的实体及其关系。

    Args:
        entity_name: 实体名称
        include_neighbors: 是否包含邻居实体
        hops: 邻居跳数（1-3）
        knowledge_base_id: 可选；要查询的已授权团队知识库 ID
    """
    kb = _get_kb()
    principal = await _principal_for_knowledge_base(
        await _get_mcp_principal(), knowledge_base_id
    )
    entity = await kb.get_entity(entity_name, principal.team_id)
    if not entity:
        return {"error": f"实体不存在: {entity_name}"}

    if include_neighbors:
        neighbors = await kb.get_neighbors(entity_name, hops, principal.team_id)
        entity["neighbors"] = neighbors

    return entity


@mcp.tool()
async def upload_document(
    file_name: str,
    content: str,
    tags: list[str] | None = None,
    scope: str = "team",
    idempotency_key: str | None = None,
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    """上传文档到知识库（支持 markdown 文本直接上传）。

    Args:
        file_name: 文件名（含扩展名，如 report.md）
        content: 文件文本内容
        knowledge_base_id: 可选；目标已授权团队知识库 ID
    """
    kb = _get_kb()
    principal = await _principal_for_knowledge_base(
        await _get_mcp_principal(), knowledge_base_id
    )
    _require_mcp_write(principal)
    async with async_session_factory() as session:
        return await kb.upload_file(
            session,
            file_name,
            content.encode("utf-8"),
            team_id=principal.team_id,
            tags=tags,
            scope=scope,
            idempotency_key=idempotency_key,
        )


@mcp.tool()
async def get_operation(
    operation_id: str, knowledge_base_id: str | None = None
) -> dict[str, Any]:
    """查询持久异步 Operation 的状态、进度和错误。"""
    kb = _get_kb()
    principal = await _principal_for_knowledge_base(
        await _get_mcp_principal(), knowledge_base_id
    )
    async with async_session_factory() as session:
        result = await kb.operations.get(
            session, principal.team_id, uuid.UUID(operation_id)
        )
        if not result:
            return {"error": f"Operation 不存在: {operation_id}"}
        return result
