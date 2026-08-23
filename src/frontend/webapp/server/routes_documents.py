"""BFF document routes: browse / get / upload / delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/documents", tags=["documents"])


class EditContentRequest(BaseModel):
    content: str


@router.get("")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    file_type: str | None = None,
    status: str | None = None,
    engine: EngineClient = Depends(deps.get_engine),
):
    return await engine.list_documents(page, page_size, file_type, status)


@router.get("/{doc_id}")
async def get_document(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    out = await engine.get_document(doc_id)
    if out.get("error"):
        raise HTTPException(404, out["error"])
    return out


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    return await engine.ingest(file.filename, data)


@router.put("/{doc_id}/content")
async def edit_document_content(
    doc_id: str,
    body: EditContentRequest,
    engine: EngineClient = Depends(deps.get_engine),
):
    try:
        return await engine.edit_content(doc_id, body.content)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.remove(doc_id)
