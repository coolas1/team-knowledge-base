"""REST API 路由。"""

from __future__ import annotations

import uuid
import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.knowledge_base import KnowledgeBase
from src.core.operations import IdempotencyConflict
from src.api.context import RequestPrincipal, get_principal, normalize_ollama_username, require_write
from src.api.context import hash_api_token
from src.db.models import Document, Team, TeamApiToken, TrustedOllamaAccount
from src.db.postgres import get_session

router = APIRouter()

# ── 依赖注入 ────────────────────────────────────────────────────

_kb_instance: KnowledgeBase | None = None


def set_kb(kb: KnowledgeBase) -> None:
    global _kb_instance
    _kb_instance = kb


def get_kb() -> KnowledgeBase:
    if _kb_instance is None:
        raise RuntimeError("KnowledgeBase 未初始化")
    return _kb_instance


# ── Schemas ─────────────────────────────────────────────────────


class EditContentRequest(BaseModel):
    content: str


class SearchRequest(BaseModel):
    query: str
    tags: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class AdminTokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["member"])
    expires_at: datetime | None = None


class AdminTokenUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    roles: list[str] | None = None
    active: bool | None = None
    expires_at: datetime | None = None


class AdminOllamaAccountCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


class AdminOllamaAccountUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    roles: list[str] | None = None
    active: bool | None = None


def _validate_roles(roles: list[str]) -> list[str]:
    allowed = {"viewer", "member", "admin"}
    normalized = sorted(set(roles))
    if not normalized or not set(normalized).issubset(allowed):
        raise HTTPException(400, "roles 仅支持 viewer/member/admin，且不能为空")
    return normalized


def _serialize_admin_token(token: TeamApiToken) -> dict[str, Any]:
    return {
        "id": str(token.id),
        "team_id": token.team_id,
        "name": token.name,
        "subject": token.subject,
        "token_prefix": token.token_prefix,
        "roles": token.roles,
        "active": token.active,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "created_at": token.created_at.isoformat() if token.created_at else None,
    }


def _serialize_ollama_account(account: TrustedOllamaAccount) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "team_id": account.team_id,
        "username": account.username,
        "display_name": account.display_name,
        "roles": account.roles,
        "active": account.active,
        "last_used_at": account.last_used_at.isoformat() if account.last_used_at else None,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "updated_at": account.updated_at.isoformat() if account.updated_at else None,
    }


# ── 文件端点 ────────────────────────────────────────────────────


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
    tags: list[str] = Query(default=[]),
    scope: str = Query("team"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """上传文件 → 返回 doc_id + status。"""
    require_write(principal)
    content = await file.read()
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    try:
        return await kb.upload_file(
            session,
            file.filename,
            content,
            team_id=principal.team_id,
            tags=tags,
            scope=scope,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/documents/{doc_id}/content")
async def edit_content(
    doc_id: uuid.UUID,
    body: EditContentRequest,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """在线编辑 md → 触发 re-index。"""
    require_write(principal)
    try:
        return await kb.edit_content(
            session, doc_id, body.content, principal.team_id, idempotency_key
        )
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """文件详情（含 status、overview、chunk 数量）。"""
    result = await kb.get_document(session, doc_id, principal.team_id)
    if not result:
        raise HTTPException(404, "文档不存在")
    return result


@router.get("/documents")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
    tags: list[str] = Query(default=[]),
    scope: str | None = None,
) -> dict[str, Any]:
    """文件列表（分页，按 type/status 筛选）。"""
    return await kb.list_documents(
        session,
        page,
        page_size,
        file_type,
        status,
        team_id=principal.team_id,
        tags=tags,
        scope=scope,
    )


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """删除文件（级联删 chunks + Neo4j）。"""
    require_write(principal)
    deleted = await kb.delete_document(session, doc_id, principal.team_id)
    if not deleted:
        raise HTTPException(404, "文档不存在")
    return {"deleted": True, "id": str(doc_id)}


# ── 搜索端点 ────────────────────────────────────────────────────


@router.post("/search")
async def search(
    body: SearchRequest,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """语义检索（向量粗筛 → Reranker 守门 → 图谱增强）。"""
    result = await kb.search(
        session,
        body.query,
        team_id=principal.team_id,
        tags=body.tags,
        scopes=body.scopes,
    )
    return {
        "chunks": [
            {
                "doc_id": c.doc_id,
                "title": c.title,
                "chunk_text": c.chunk_text,
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


# ── 图谱端点 ────────────────────────────────────────────────────


@router.get("/graph/full")
async def get_full_graph(
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """返回全图数据（所有实体 + 关系），供前端力导向图渲染。"""
    return await kb.get_full_graph(principal.team_id)


@router.get("/graph/entity/{name}")
async def get_entity(
    name: str,
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """查询实体详情 + 关联关系。"""
    result = await kb.get_entity(name, principal.team_id)
    if not result:
        raise HTTPException(404, f"实体不存在: {name}")
    return result


@router.get("/graph/neighbors/{name}")
async def get_neighbors(
    name: str,
    hops: int = Query(2, ge=1, le=3),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """获取实体 N 跳邻居。"""
    results = await kb.get_neighbors(name, hops, principal.team_id)
    return {"name": name, "hops": hops, "neighbors": results}


# ── 持久 Operation ─────────────────────────────────────────────


@router.get("/operations")
async def list_operations(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    items = await kb.operations.list(session, principal.team_id, status, limit)
    return {"items": items}


@router.get("/operations/{operation_id}")
async def get_operation(
    operation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    result = await kb.operations.get(session, principal.team_id, operation_id)
    if not result:
        raise HTTPException(404, "Operation 不存在")
    return result


@router.post("/operations/{operation_id}/retry")
async def retry_operation(
    operation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    require_write(principal)
    try:
        result = await kb.operations.retry(session, principal.team_id, operation_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Operation 不存在")
    return result


@router.post("/operations/{operation_id}/cancel")
async def cancel_operation(
    operation_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    require_write(principal)
    try:
        result = await kb.operations.cancel(session, principal.team_id, operation_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Operation 不存在")
    return result


@router.get("/auth/me")
async def get_current_identity(
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    team = await session.get(Team, principal.team_id)
    document_count = await session.scalar(
        select(func.count(Document.id)).where(
            or_(Document.team_id == principal.team_id, Document.scope == "public")
        )
    )
    return {
        "team_id": principal.team_id,
        "team_name": team.name if team else principal.team_id,
        "subject": principal.subject,
        "roles": list(principal.roles),
        "auth_source": principal.auth_source,
        "knowledge_base": {
            "id": principal.team_id,
            "name": team.name if team else principal.team_id,
            "document_count": document_count or 0,
        },
    }


@router.get("/knowledge-bases")
async def list_accessible_knowledge_bases(
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    team = await session.get(Team, principal.team_id)
    document_count = await session.scalar(
        select(func.count(Document.id)).where(
            or_(Document.team_id == principal.team_id, Document.scope == "public")
        )
    )
    return {
        "current_team_id": principal.team_id,
        "items": [{
            "id": principal.team_id,
            "name": team.name if team else principal.team_id,
            "document_count": document_count or 0,
            "includes_public_documents": True,
            "roles": list(principal.roles),
        }],
    }


def _require_admin(principal: RequestPrincipal) -> None:
    if "admin" not in principal.roles:
        raise HTTPException(403, "需要 admin 角色")


@router.get("/admin/team")
async def get_admin_team(
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    team = await session.get(Team, principal.team_id)
    document_count = await session.scalar(
        select(func.count(Document.id)).where(
            or_(Document.team_id == principal.team_id, Document.scope == "public")
        )
    )
    public_document_count = await session.scalar(
        select(func.count(Document.id)).where(Document.scope == "public")
    )
    managed_token_count = await session.scalar(
        select(func.count(TeamApiToken.id)).where(TeamApiToken.team_id == principal.team_id)
    )
    trusted_ollama_account_count = await session.scalar(
        select(func.count(TrustedOllamaAccount.id)).where(
            TrustedOllamaAccount.team_id == principal.team_id
        )
    )
    return {
        "id": principal.team_id,
        "name": team.name if team else principal.team_id,
        "active": team.active if team else True,
        "document_count": document_count or 0,
        "public_document_count": public_document_count or 0,
        "managed_token_count": managed_token_count or 0,
        "trusted_ollama_account_count": trusted_ollama_account_count or 0,
        "current_subject": principal.subject,
        "current_roles": list(principal.roles),
        "auth_source": principal.auth_source,
    }


@router.get("/admin/tokens")
async def list_admin_tokens(
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    tokens = (
        await session.scalars(
            select(TeamApiToken)
            .where(TeamApiToken.team_id == principal.team_id)
            .order_by(TeamApiToken.created_at.desc())
        )
    ).all()
    return {"team_id": principal.team_id, "items": [_serialize_admin_token(token) for token in tokens]}


@router.post("/admin/tokens")
async def create_admin_token(
    body: AdminTokenCreateRequest,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    raw_token = f"tkb_{secrets.token_urlsafe(32)}"
    token = TeamApiToken(
        team_id=principal.team_id,
        name=body.name.strip(),
        subject=body.subject.strip(),
        token_hash=hash_api_token(raw_token),
        token_prefix=raw_token[:12],
        roles=_validate_roles(body.roles),
        expires_at=body.expires_at,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return {
        **_serialize_admin_token(token),
        "token": raw_token,
        "warning": "Token 只显示一次，请立即安全保存",
    }


@router.patch("/admin/tokens/{token_id}")
async def update_admin_token(
    token_id: uuid.UUID,
    body: AdminTokenUpdateRequest,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    token = await session.scalar(select(TeamApiToken).where(
        TeamApiToken.id == token_id, TeamApiToken.team_id == principal.team_id
    ))
    if token is None:
        raise HTTPException(404, "Token 不存在")
    if principal.token_id == str(token.id) and body.active is False:
        raise HTTPException(400, "不能停用当前正在使用的 Token")
    if body.name is not None:
        token.name = body.name.strip()
    if body.roles is not None:
        token.roles = _validate_roles(body.roles)
    if body.active is not None:
        token.active = body.active
    if "expires_at" in body.model_fields_set:
        token.expires_at = body.expires_at
    await session.commit()
    await session.refresh(token)
    return _serialize_admin_token(token)


@router.delete("/admin/tokens/{token_id}")
async def revoke_admin_token(
    token_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    token = await session.scalar(select(TeamApiToken).where(
        TeamApiToken.id == token_id, TeamApiToken.team_id == principal.team_id
    ))
    if token is None:
        raise HTTPException(404, "Token 不存在")
    if principal.token_id == str(token.id):
        raise HTTPException(400, "不能撤销当前正在使用的 Token")
    token.active = False
    await session.commit()
    return {"revoked": True, "id": str(token.id), "team_id": principal.team_id}


@router.get("/admin/ollama-accounts")
async def list_admin_ollama_accounts(
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    accounts = (
        await session.scalars(
            select(TrustedOllamaAccount)
            .where(TrustedOllamaAccount.team_id == principal.team_id)
            .order_by(TrustedOllamaAccount.created_at.desc())
        )
    ).all()
    return {
        "team_id": principal.team_id,
        "items": [_serialize_ollama_account(account) for account in accounts],
    }


@router.post("/admin/ollama-accounts")
async def create_admin_ollama_account(
    body: AdminOllamaAccountCreateRequest,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    username = normalize_ollama_username(body.username)
    if not username:
        raise HTTPException(400, "Ollama 用户名不能为空")
    account = await session.scalar(
        select(TrustedOllamaAccount).where(
            TrustedOllamaAccount.team_id == principal.team_id,
            TrustedOllamaAccount.username == username,
        )
    )
    if account is None:
        account = TrustedOllamaAccount(
            team_id=principal.team_id,
            username=username,
            display_name=body.display_name.strip(),
            roles=_validate_roles(body.roles),
        )
        session.add(account)
    else:
        account.display_name = body.display_name.strip()
        account.roles = _validate_roles(body.roles)
        account.active = True
    await session.commit()
    await session.refresh(account)
    return _serialize_ollama_account(account)


@router.patch("/admin/ollama-accounts/{account_id}")
async def update_admin_ollama_account(
    account_id: uuid.UUID,
    body: AdminOllamaAccountUpdateRequest,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    account = await session.scalar(
        select(TrustedOllamaAccount).where(
            TrustedOllamaAccount.id == account_id,
            TrustedOllamaAccount.team_id == principal.team_id,
        )
    )
    if account is None:
        raise HTTPException(404, "可信 Ollama 账号不存在")
    if body.display_name is not None:
        account.display_name = body.display_name.strip()
    if body.roles is not None:
        account.roles = _validate_roles(body.roles)
    if body.active is not None:
        account.active = body.active
    await session.commit()
    await session.refresh(account)
    return _serialize_ollama_account(account)


@router.delete("/admin/ollama-accounts/{account_id}")
async def revoke_admin_ollama_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    account = await session.scalar(
        select(TrustedOllamaAccount).where(
            TrustedOllamaAccount.id == account_id,
            TrustedOllamaAccount.team_id == principal.team_id,
        )
    )
    if account is None:
        raise HTTPException(404, "可信 Ollama 账号不存在")
    account.active = False
    await session.commit()
    return {"revoked": True, "id": str(account.id), "team_id": principal.team_id}


@router.post("/admin/projection/rebuild")
async def rebuild_projection(
    document_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    count = await kb.projector.rebuild(session, principal.team_id, document_id)
    return {"queued": count, "team_id": principal.team_id}


@router.get("/admin/projection/reconcile")
async def reconcile_projection(
    session: AsyncSession = Depends(get_session),
    kb: KnowledgeBase = Depends(get_kb),
    principal: RequestPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    _require_admin(principal)
    return await kb.projector.reconcile(session, principal.team_id)
