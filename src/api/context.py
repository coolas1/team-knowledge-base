"""可信请求上下文与 team 隔离边界。"""

from __future__ import annotations

import json
import hashlib
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.config import settings
from src.db.models import TeamApiToken, TrustedOllamaAccount
from src.db.postgres import async_session_factory

READ_ROLES = frozenset({"viewer", "member", "admin"})
WRITE_ROLES = frozenset({"member", "admin"})


@dataclass(frozen=True)
class RequestPrincipal:
    team_id: str
    subject: str = "anonymous"
    roles: tuple[str, ...] = ("member",)
    auth_source: str = "anonymous"
    token_id: str | None = None
    account_id: str | None = None
    accessible_team_ids: tuple[str, ...] = ()


def can_read(principal: RequestPrincipal) -> bool:
    return bool(READ_ROLES.intersection(principal.roles))


def can_write(principal: RequestPrincipal) -> bool:
    return bool(WRITE_ROLES.intersection(principal.roles))


def require_write(principal: RequestPrincipal) -> None:
    if not can_write(principal):
        raise HTTPException(403, "当前用户只有只读权限")


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_ollama_username(username: str) -> str:
    return username.strip().casefold()


def get_forwarded_ollama_username(headers: Mapping[str, str]) -> str | None:
    """Return a concrete OpenWebUI/Ollama username from trusted proxy headers.

    OpenWebUI global connections can preserve an unexpanded ``{{USER_NAME}}``
    template in a custom header.  Prefer its official forwarded identity header
    and ignore template literals instead of treating them as real usernames.
    """
    for name in (
        "x-openwebui-user-name",
        "x-open-webui-user-name",
        "x-tkb-ollama-user",
        "x-ollama-user",
    ):
        value = headers.get(name)
        if value:
            candidate = value.strip()
            if candidate and not (candidate.startswith("{{") and candidate.endswith("}}")):
                return candidate
    return None


def is_trusted_ollama_source(host: str | None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
        networks = [
            ipaddress.ip_network(item.strip(), strict=False)
            for item in settings.trusted_ollama_networks.split(",")
            if item.strip()
        ]
    except ValueError:
        return False
    return any(address in network for network in networks)


def _configured_token_principal(token: str) -> RequestPrincipal | None:
    try:
        mapping = json.loads(settings.api_tokens_json or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("API_TOKENS_JSON 不是有效 JSON") from exc
    entry = mapping.get(token)
    if entry is None:
        return None
    if isinstance(entry, str):
        return RequestPrincipal(team_id=entry, subject="api-token", auth_source="configuration")
    return RequestPrincipal(
        team_id=entry["team_id"],
        subject=entry.get("subject", "api-token"),
        roles=tuple(entry.get("roles", ["member"])),
        auth_source="configuration",
    )


async def resolve_token_principal(
    token: str, session: AsyncSession | None = None, *, update_last_used: bool = True
) -> RequestPrincipal | None:
    configured = _configured_token_principal(token)
    if configured is not None:
        return configured

    owns_session = session is None
    if session is None:
        session = async_session_factory()
    try:
        now = datetime.now(timezone.utc)
        record = await session.scalar(
            select(TeamApiToken).where(
                TeamApiToken.token_hash == hash_api_token(token),
                TeamApiToken.active.is_(True),
            )
        )
        if record is None or (record.expires_at is not None and record.expires_at <= now):
            return None
        if update_last_used:
            record.last_used_at = now
            await session.commit()
        return RequestPrincipal(
            team_id=record.team_id,
            subject=record.subject,
            roles=tuple(record.roles),
            auth_source="database",
            token_id=str(record.id),
        )
    finally:
        if owns_session:
            await session.close()


async def resolve_trusted_ollama_principal(
    username: str,
    requested_team_id: str | None = None,
    session: AsyncSession | None = None,
    *,
    update_last_used: bool = True,
) -> RequestPrincipal | None:
    """Resolve a trusted Ollama username to an approved team membership."""
    normalized = normalize_ollama_username(username)
    if not normalized:
        return None

    owns_session = session is None
    if session is None:
        session = async_session_factory()
    try:
        records = (
            await session.scalars(
                select(TrustedOllamaAccount)
                .where(
                    TrustedOllamaAccount.username == normalized,
                    TrustedOllamaAccount.active.is_(True),
                )
                .order_by(TrustedOllamaAccount.team_id)
            )
        ).all()
        if not records:
            return None
        selected = next(
            (record for record in records if record.team_id == requested_team_id),
            None,
        ) if requested_team_id else records[0]
        if selected is None:
            return None
        if update_last_used:
            selected.last_used_at = datetime.now(timezone.utc)
            await session.commit()
        return RequestPrincipal(
            team_id=selected.team_id,
            subject=selected.username,
            roles=tuple(selected.roles),
            auth_source="ollama-account",
            account_id=str(selected.id),
            accessible_team_ids=tuple(record.team_id for record in records),
        )
    finally:
        if owns_session:
            await session.close()


async def resolve_single_trusted_ollama_principal(
    requested_team_id: str | None = None,
) -> RequestPrincipal | None:
    """Use the sole configured Ollama username for trusted local callers."""
    async with async_session_factory() as session:
        usernames = (
            await session.scalars(
                select(TrustedOllamaAccount.username)
                .where(TrustedOllamaAccount.active.is_(True))
                .distinct()
                .limit(2)
            )
        ).all()
        if len(usernames) != 1:
            return None
        return await resolve_trusted_ollama_principal(
            usernames[0], requested_team_id, session=session
        )


async def get_principal(
    authorization: str | None = Header(default=None),
    x_team_id: str | None = Header(default=None, alias="X-Team-ID"),
) -> RequestPrincipal:
    principal: RequestPrincipal | None = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Authorization 必须使用 Bearer token")
        principal = await resolve_token_principal(token)
        if principal is None:
            raise HTTPException(401, "无效 API token")
    elif settings.allow_anonymous_default_team:
        principal = RequestPrincipal(team_id=settings.default_team_id)
    else:
        raise HTTPException(401, "需要认证")

    if x_team_id and x_team_id != principal.team_id:
        if not settings.allow_untrusted_team_header:
            raise HTTPException(403, "请求 team 与认证身份不匹配")
        principal = RequestPrincipal(
            team_id=x_team_id,
            subject=principal.subject,
            roles=principal.roles,
            auth_source=principal.auth_source,
            token_id=principal.token_id,
        )
    if not can_read(principal):
        raise HTTPException(403, "当前用户没有知识库读取权限")
    return principal
