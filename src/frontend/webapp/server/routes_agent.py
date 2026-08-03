"""BFF agent-skill routes: invoke harness-agnostic skills in-process."""
from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.agent.interface import EngineClient, LlmClient, SkillContext
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/agent", tags=["agent"])


class AskRequest(BaseModel):
    query: str
    top_k: int = 10


class AgentMessageRequest(BaseModel):
    message: str


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
):
    try:
        async with _pi_client() as client:
            response = await client.request(
                method,
                f"{_pi_agent_url()}{path}",
                json=body,
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


def _find_skill(name: str):
    plugin = deps.get_plugin()
    for s in plugin.skills():
        if s.name == name:
            return s
    raise HTTPException(404, f"skill not found: {name}")


@router.post("/ask")
async def ask(
    body: AskRequest,
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    skill = _find_skill("search_and_answer")
    ctx = SkillContext(engine=engine, llm=llm, params={"query": body.query, "top_k": body.top_k})
    result = await skill.run(ctx)
    return result.output


@router.post("/sessions", status_code=201)
async def create_agent_session():
    return await _proxy_json("POST", "/v1/sessions")


@router.get("/sessions")
async def list_agent_sessions():
    return await _proxy_json("GET", "/v1/sessions")


@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str):
    session_id = _checked_session_id(session_id)
    return await _proxy_json("GET", f"/v1/sessions/{session_id}")


@router.delete("/sessions/{session_id}")
async def delete_agent_session(session_id: str):
    session_id = _checked_session_id(session_id)
    return await _proxy_json("DELETE", f"/v1/sessions/{session_id}")


@router.post("/sessions/{session_id}/cancel")
async def cancel_agent_session(session_id: str):
    session_id = _checked_session_id(session_id)
    return await _proxy_json("POST", f"/v1/sessions/{session_id}/cancel")


@router.post("/sessions/{session_id}/messages")
async def stream_agent_message(session_id: str, body: AgentMessageRequest):
    session_id = _checked_session_id(session_id)
    if not body.message.strip():
        raise HTTPException(400, "message must not be empty")

    client = _pi_client()
    try:
        request = client.build_request(
            "POST",
            f"{_pi_agent_url()}/v1/sessions/{session_id}/messages",
            json={"message": body.message},
        )
        response = await client.send(request, stream=True)
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


@router.post("/ingest-summarize")
async def ingest_summarize(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
    llm: LlmClient | None = Depends(deps.get_llm),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    skill = _find_skill("ingest_and_summarize")
    ctx = SkillContext(engine=engine, llm=llm, params={"name": file.filename, "data": data})
    result = await skill.run(ctx)
    return result.output
