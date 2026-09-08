"""Webapp host agent routes: invoke plugin skills in-process AND proxy to the
optional Pi Agent runtime (session management, SSE streaming)."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agent.interface import LlmClient, SkillContext
from src.frontend.webapp.server import deps


def _scope_headers(request: Request) -> dict[str, str]:
    deps._binding(
        request
    )  # Validate before forwarding; body/query fields have no authority.
    token = request.headers.get("x-tkb-scope-token")
    return {"X-TKB-Scope-Token": token} if token is not None else {}


router = APIRouter(prefix="/agent", tags=["agent"])


# ── In-process skill routes ──────────────────────────────────────────


class AskRequest(BaseModel):
    query: str
    top_k: int = 10


def _get_skill(name: str):
    plugin = deps.get_plugin()
    skill = plugin.skills.get(name)
    if skill is None:
        raise HTTPException(404, f"skill not found: {name}")
    return skill


@router.post("/ask")
async def ask(
    body: AskRequest,
    kb=Depends(deps.get_kb),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    skill = _get_skill("search_and_answer")
    ctx = SkillContext(
        kb=kb, llm=llm, params={"query": body.query, "top_k": body.top_k}
    )
    return (await skill.run(ctx)).output


@router.post("/ingest-summarize")
async def ingest_summarize(
    file: UploadFile = File(...),
    kb=Depends(deps.get_kb),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    skill = _get_skill("ingest_and_summarize")
    ctx = SkillContext(kb=kb, llm=llm, params={"name": file.filename, "data": data})
    return (await skill.run(ctx)).output


# ── Pi Agent proxy routes ────────────────────────────────────────────


class AgentMessageRequest(BaseModel):
    message: str
    client_message_id: str | None = Field(default=None, alias="clientMessageId")


_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _pi_agent_url() -> str:
    return os.getenv("PI_AGENT_URL", "http://127.0.0.1:8010").rstrip("/")


def _pi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0)
    )


def _checked_session_id(session_id: str) -> str:
    if not _SESSION_ID.fullmatch(session_id):
        raise HTTPException(400, "invalid agent session id")
    return session_id


def _upstream_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return response.text[:500] or "Pi Agent request failed"
    if isinstance(body, dict):
        return str(body.get("error") or body.get("detail") or "Pi Agent request failed")
    return "Pi Agent request failed"


async def _proxy_json(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
):
    try:
        async with _pi_client() as client:
            response = await client.request(
                method,
                f"{_pi_agent_url()}{path}",
                json=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(503, "Pi Agent 当前不可用") from exc
    if response.is_error:
        status = response.status_code if response.status_code < 500 else 502
        raise HTTPException(status, _upstream_error(response))
    return response.json()


async def _relay_sse(
    response: httpx.Response,
    client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


@router.post("/sessions", status_code=201)
async def create_agent_session(request: Request):
    return await _proxy_json("POST", "/v1/sessions", headers=_scope_headers(request))


@router.get("/sessions")
async def list_agent_sessions(request: Request):
    return await _proxy_json("GET", "/v1/sessions", headers=_scope_headers(request))


@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str, request: Request):
    session_id = _checked_session_id(session_id)
    return await _proxy_json(
        "GET", f"/v1/sessions/{session_id}", headers=_scope_headers(request)
    )


@router.delete("/sessions/{session_id}")
async def delete_agent_session(session_id: str, request: Request):
    session_id = _checked_session_id(session_id)
    return await _proxy_json(
        "DELETE", f"/v1/sessions/{session_id}", headers=_scope_headers(request)
    )


@router.delete("/sessions/{session_id}/memory")
async def forget_agent_session_memory(session_id: str, request: Request):
    session_id = _checked_session_id(session_id)
    return await _proxy_json(
        "DELETE", f"/v1/sessions/{session_id}/memory", headers=_scope_headers(request)
    )


@router.post("/sessions/{session_id}/cancel")
async def cancel_agent_session(session_id: str, request: Request):
    session_id = _checked_session_id(session_id)
    return await _proxy_json(
        "POST", f"/v1/sessions/{session_id}/cancel", headers=_scope_headers(request)
    )


@router.post("/sessions/{session_id}/messages")
async def stream_agent_message(
    session_id: str, body: AgentMessageRequest, request: Request
):
    scope_headers = _scope_headers(request)
    session_id = _checked_session_id(session_id)
    if not body.message.strip():
        raise HTTPException(400, "message must not be empty")

    client = _pi_client()
    try:
        upstream_request = client.build_request(
            "POST",
            f"{_pi_agent_url()}/v1/sessions/{session_id}/messages",
            headers=scope_headers,
            json={
                "message": body.message,
                **(
                    {"clientMessageId": body.client_message_id}
                    if body.client_message_id
                    else {}
                ),
            },
        )
        response = await client.send(upstream_request, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(503, "Pi Agent 当前不可用") from exc

    if response.is_error:
        await response.aread()
        detail = _upstream_error(response)
        status = response.status_code if response.status_code < 500 else 502
        await response.aclose()
        await client.aclose()
        raise HTTPException(status, detail)

    return StreamingResponse(
        _relay_sse(response, client),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
