"""Revision-bound review controls; model tools cannot approve on a user's behalf."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.agent.ppt.contracts import Budget, DeckSpec
from src.agent.ppt.runtime import authority, get_store
from . import deps

router = APIRouter(prefix="/ppt/jobs", tags=["presentations"])


class CreateBody(BaseModel):
    spec: DeckSpec
    session_id: str = Field(default="web", max_length=200)
    budget: Budget | None = None


class ControlBody(BaseModel):
    revision: int = Field(ge=1)
    action: Literal[
        "approve_outline", "approve_sample", "cancel", "retry", "revise", "budget"
    ]
    page: int | None = None
    spec: DeckSpec | None = None
    budget: Budget | None = None


def failure(error):
    if isinstance(error, PermissionError):
        return HTTPException(403, "PPT 任务或来源不可访问")
    return HTTPException(409, str(error))


@router.post("", status_code=202)
async def create(body: CreateBody, request: Request):
    try:
        identifier = await get_store().create(
            body.spec,
            deps._binding(request),
            authority(request.headers),
            body.session_id,
            body.budget,
        )
        return {"id": identifier, "review_url": f"/ppt/{identifier}"}
    except (ValueError, PermissionError) as error:
        raise failure(error) from error


@router.get("/{identifier}")
async def status(identifier: str, request: Request):
    try:
        return await get_store().get(
            identifier, deps._binding(request), authority(request.headers)
        )
    except (ValueError, PermissionError) as error:
        raise failure(error) from error


@router.post("/{identifier}/control")
async def control(identifier: str, body: ControlBody, request: Request):
    try:
        await get_store().control(
            identifier,
            deps._binding(request),
            authority(request.headers),
            body.revision,
            body.action,
            page=body.page,
            spec=body.spec,
            budget=body.budget,
        )
        return await status(identifier, request)
    except (ValueError, PermissionError) as error:
        raise failure(error) from error


@router.get("/{identifier}/pages/{page}")
async def preview(identifier: str, page: int, request: Request):
    try:
        path = await get_store().file(
            identifier, deps._binding(request), authority(request.headers), page=page
        )
        return FileResponse(path, headers={"Cache-Control": "private, no-store"})
    except (ValueError, PermissionError) as error:
        raise failure(error) from error


@router.get("/{identifier}/download")
async def download(identifier: str, request: Request):
    try:
        path = await get_store().file(
            identifier,
            deps._binding(request),
            authority(request.headers),
            artifact=True,
        )
        return FileResponse(
            path,
            filename="presentation.pptx",
            headers={"Cache-Control": "private, no-store"},
        )
    except (ValueError, PermissionError) as error:
        raise failure(error) from error


@router.get("/{identifier}/references/{document_id}")
async def reference(identifier: str, document_id: str, request: Request):
    try:
        path = await get_store().reference_file(
            identifier, document_id, deps._binding(request), authority(request.headers)
        )
        return FileResponse(path, headers={"Cache-Control": "private, no-store"})
    except (ValueError, PermissionError) as error:
        raise failure(error) from error
