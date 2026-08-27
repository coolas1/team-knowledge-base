"""BFF document routes: browse / get / upload / delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/documents", tags=["documents"])


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


@router.get("/{doc_id}/versions")
async def list_versions(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    try:
        return await engine.list_versions(doc_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/{doc_id}/versions/diff")
async def diff_versions(
    doc_id: str,
    from_version: int = Query(..., ge=1),
    to_version: int = Query(..., ge=1),
    engine: EngineClient = Depends(deps.get_engine),
):
    try:
        return await engine.diff_versions(doc_id, from_version, to_version)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    engine: EngineClient = Depends(deps.get_engine),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")
    data = await file.read()
    return await engine.ingest(file.filename, data)


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, engine: EngineClient = Depends(deps.get_engine)):
    return await engine.remove(doc_id)
