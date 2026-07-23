"""Minimal read-only OpenAPI tool surface for Open WebUI."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from src.api.context import (
    RequestPrincipal,
    can_read,
    get_forwarded_ollama_username,
    is_trusted_ollama_source,
    resolve_single_trusted_ollama_principal,
    resolve_token_principal,
    resolve_trusted_ollama_principal,
)
from src.core.knowledge_base import KnowledgeBase
from src.db.models import Document, Team, TrustedOllamaAccount
from src.db.postgres import async_session_factory

router = APIRouter()
_kb: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb
    _kb = kb


def _get_kb() -> KnowledgeBase:
    if _kb is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb


async def _get_principal(request: Request) -> RequestPrincipal:
    authorization = request.headers.get("authorization")
    source_host = request.client.host if request.client else None
    trusted_openwebui = is_trusted_ollama_source(source_host)
    principal: RequestPrincipal | None = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            principal = await resolve_token_principal(token)
        elif not trusted_openwebui:
            raise HTTPException(401, "Authorization 必须使用 Bearer token")

    # OpenWebUI may attach its own login-session Authorization header even when
    # an external tool is configured with auth=none.  That token is not a TKB
    # API token, so trusted OpenWebUI traffic must continue with its forwarded
    # username instead of failing before the user identity is evaluated.
    if principal is None:
        if not trusted_openwebui:
            if authorization:
                raise HTTPException(401, "无效 API token")
            raise HTTPException(403, "Open WebUI 请求不来自受信网络")
        username = get_forwarded_ollama_username(request.headers)
        requested_team_id = request.headers.get("x-tkb-team")
        if username:
            principal = await resolve_trusted_ollama_principal(username, requested_team_id)
        else:
            principal = await resolve_single_trusted_ollama_principal(requested_team_id)
    if principal is None:
        raise HTTPException(401, "没有匹配的可信 Ollama 账号")
    if not can_read(principal):
        raise HTTPException(403, "当前 Ollama 账号没有读取权限")
    return principal


async def _select_team(
    principal: RequestPrincipal, knowledge_base_id: str | None
) -> RequestPrincipal:
    if not knowledge_base_id or knowledge_base_id == principal.team_id:
        return principal
    if (
        principal.auth_source != "ollama-account"
        or knowledge_base_id not in principal.accessible_team_ids
    ):
        raise HTTPException(403, f"当前账号无权访问知识库: {knowledge_base_id}")
    selected = await resolve_trusted_ollama_principal(
        principal.subject, knowledge_base_id
    )
    if selected is None or not can_read(selected):
        raise HTTPException(403, f"当前账号无权访问知识库: {knowledge_base_id}")
    return selected


class KnowledgeBaseRequest(BaseModel):
    knowledge_base_id: str | None = Field(
        default=None,
        description=(
            "可选团队知识库 ID；留空时搜索当前账号可访问的全部团队及公共文档"
        ),
    )


class SearchRequest(KnowledgeBaseRequest):
    query: str = Field(description="要检索的问题或关键词")
    tags: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    include_graph: bool = Field(
        default=True,
        description="是否执行不影响 chunk 排名的 Neo4j 实体与关联文档增强",
    )


class DocumentRequest(KnowledgeBaseRequest):
    doc_id: str = Field(description="文档 UUID")


class GraphRequest(KnowledgeBaseRequest):
    entity_name: str = Field(description="实体名称")
    include_neighbors: bool = True
    hops: int = Field(default=2, ge=1, le=3)


@router.post(
    "/get_current_context",
    operation_id="tkb_get_current_context",
    summary="获取当前可信 Ollama 用户、默认团队和角色",
)
async def get_current_context(request: Request) -> dict[str, Any]:
    principal = await _get_principal(request)
    return {
        "team_id": principal.team_id,
        "subject": principal.subject,
        "roles": list(principal.roles),
        "auth_source": principal.auth_source,
        "accessible_team_ids": list(principal.accessible_team_ids or (principal.team_id,)),
    }


@router.post(
    "/list_knowledge_bases",
    operation_id="tkb_list_knowledge_bases",
    summary="列出当前 Ollama 账号可访问的团队知识库及公共文档数量",
)
async def list_knowledge_bases(request: Request) -> dict[str, Any]:
    principal = await _get_principal(request)
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
            access = [(item.team_id, list(item.roles)) for item in memberships]
        else:
            access = [(principal.team_id, list(principal.roles))]
        public_count = await session.scalar(
            select(func.count(Document.id)).where(Document.scope == "public")
        )
        items = []
        for team_id, roles in access:
            team = await session.get(Team, team_id)
            visible_count = await session.scalar(
                select(func.count(Document.id)).where(
                    or_(Document.team_id == team_id, Document.scope == "public")
                )
            )
            items.append({
                "id": team_id,
                "name": team.name if team else team_id,
                "document_count": visible_count or 0,
                "public_document_count": public_count or 0,
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


@router.post(
    "/search",
    operation_id="tkb_search",
    summary="默认检索全部可访问团队及公共知识，也可限定一个团队",
)
async def search(body: SearchRequest, request: Request) -> dict[str, Any]:
    base_principal = await _get_principal(request)
    if body.knowledge_base_id:
        principal = await _select_team(base_principal, body.knowledge_base_id)
        searched_team_ids = [principal.team_id]
        search_mode = "selected-team-and-public"
    else:
        principal = base_principal
        searched_team_ids = list(
            principal.accessible_team_ids or (principal.team_id,)
        )
        search_mode = "all-accessible-teams-and-public"
    async with async_session_factory() as session:
        result = await _get_kb().search(
            session,
            body.query,
            team_id=principal.team_id,
            team_ids=searched_team_ids,
            tags=body.tags or None,
            scopes=body.scopes or None,
            include_graph=body.include_graph,
        )
    return {
        "knowledge_base_id": body.knowledge_base_id,
        "search_mode": search_mode,
        "searched_knowledge_base_ids": searched_team_ids,
        "includes_public_documents": True,
        "chunks": [
            {
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "chunk_text": chunk.chunk_text[:1000],
                "reranker_score": chunk.reranker_score,
                "vector_score": chunk.vector_score,
                "owner_team_id": chunk.owner_team_id,
                "scope": chunk.scope,
            }
            for chunk in result.chunks
        ],
        "related_entities": result.related_entities,
        "related_docs": result.related_docs,
    }


@router.post(
    "/get_document",
    operation_id="tkb_get_document",
    summary="读取团队或公共文档正文",
)
async def get_document(body: DocumentRequest, request: Request) -> dict[str, Any]:
    principal = await _select_team(await _get_principal(request), body.knowledge_base_id)
    async with async_session_factory() as session:
        result = await _get_kb().get_document(
            session, uuid.UUID(body.doc_id), principal.team_id
        )
    if result is None:
        raise HTTPException(404, f"文档不存在: {body.doc_id}")
    return result


@router.post(
    "/query_graph",
    operation_id="tkb_query_graph",
    summary="查询指定团队图谱以及公共图谱",
)
async def query_graph(body: GraphRequest, request: Request) -> dict[str, Any]:
    principal = await _select_team(await _get_principal(request), body.knowledge_base_id)
    result = await _get_kb().get_entity(body.entity_name, principal.team_id)
    if result is None:
        raise HTTPException(404, f"实体不存在: {body.entity_name}")
    if body.include_neighbors:
        result["neighbors"] = await _get_kb().get_neighbors(
            body.entity_name, body.hops, principal.team_id
        )
    return result
