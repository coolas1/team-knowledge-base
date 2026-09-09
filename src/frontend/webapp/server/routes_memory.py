"""Scoped memory diagnostics and management routes."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.engine.interface import DirectiveDefinition, MentalModelDefinition
from src.engine.scope_policy import ScopePolicy
from src.frontend.webapp.server import deps

router = APIRouter(prefix="/memory", tags=["memory"])


def query_service(service=Depends(deps.get_query)):
    if service is None:
        raise HTTPException(503, "memory is disabled")
    return service


class MentalModelBody(BaseModel):
    name: str = Field(min_length=1)
    source_query: str = Field(min_length=1)
    description: str = ""
    tags: tuple[str, ...] = ()
    refresh_mode: Literal["full", "delta"] = "full"
    refresh_after_consolidation: bool = False
    refresh_interval_seconds: int | None = Field(default=None, ge=1)


class DirectiveBody(BaseModel):
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)
    trigger: str | None = None
    priority: int = 0
    is_active: bool = True
    tags: tuple[str, ...] = ()


class PolicyBody(BaseModel):
    expected_version: int = Field(ge=1)
    policy: ScopePolicy


class EntityCorrectionBody(BaseModel):
    source_entity_id: str
    memory_ids: list[str] = Field(min_length=1)
    target_entity_id: str | None = None
    reason: str = Field(min_length=1)


@router.get("/operations")
async def list_operations(
    session_id: str | None = None,
    turn_id: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    service=Depends(query_service),
):
    rows = await service.list_memory_operations(
        session_id=session_id, turn_id=turn_id, limit=limit
    )
    return [asdict(row) for row in rows]


@router.get("/operations/{operation_id}")
async def get_operation(operation_id: str, service=Depends(query_service)):
    row = await service.get_memory_operation(operation_id)
    if row is None:
        raise HTTPException(404, "operation not found")
    return asdict(row)


@router.post("/operations/{operation_id}/retry")
async def retry_operation(operation_id: str, service=Depends(query_service)):
    try:
        return {"changed": await service.retry_memory_operation(operation_id)}
    except KeyError as error:
        raise HTTPException(404, "operation not found") from error


@router.post("/operations/{operation_id}/cancel")
async def cancel_operation(operation_id: str, service=Depends(query_service)):
    try:
        return {"changed": await service.cancel_memory_operation(operation_id)}
    except KeyError as error:
        raise HTTPException(404, "operation not found") from error


@router.get("/facts")
async def list_facts(
    limit: int = Query(100, ge=1, le=500), service=Depends(query_service)
):
    return await service.list_memory_facts(limit=limit)


@router.get("/observations/{observation_id}")
async def observation_detail(observation_id: str, service=Depends(query_service)):
    result = await service.get_observation_detail(observation_id)
    if result is None:
        raise HTTPException(404, "observation not found")
    return result


@router.post("/entities/corrections")
async def correct_entity(body: EntityCorrectionBody, service=Depends(query_service)):
    return await service.correct_memory_entity(
        body.source_entity_id,
        body.memory_ids,
        target_entity_id=body.target_entity_id,
        reason=body.reason,
    )


@router.get("/models")
async def list_models(service=Depends(query_service)):
    return [asdict(row) for row in await service.list_mental_models()]


@router.get("/models/{model_id}")
async def get_model(model_id: str, service=Depends(query_service)):
    row = await service.get_mental_model(model_id)
    if row is None:
        raise HTTPException(404, "mental model not found")
    return asdict(row)


def _model_definition(model_id: str, body: MentalModelBody):
    return MentalModelDefinition(id=model_id, **body.model_dump())


@router.post("/models/{model_id}")
async def create_model(
    model_id: str, body: MentalModelBody, service=Depends(query_service)
):
    return asdict(await service.create_mental_model(_model_definition(model_id, body)))


@router.put("/models/{model_id}")
async def update_model(
    model_id: str, body: MentalModelBody, service=Depends(query_service)
):
    try:
        row = await service.update_mental_model(
            model_id, _model_definition(model_id, body)
        )
    except KeyError as error:
        raise HTTPException(404, "mental model not found") from error
    return asdict(row)


@router.delete("/models/{model_id}")
async def delete_model(model_id: str, service=Depends(query_service)):
    return {"deleted": await service.delete_mental_model(model_id)}


@router.post("/models/{model_id}/refresh")
async def refresh_model(model_id: str, service=Depends(query_service)):
    try:
        return {"enqueued": await service.refresh_mental_model(model_id)}
    except KeyError as error:
        raise HTTPException(404, "mental model not found") from error


@router.get("/directives")
async def list_directives(service=Depends(query_service)):
    return [asdict(row) for row in await service.list_directives()]


def _directive_definition(directive_id: str, body: DirectiveBody):
    return DirectiveDefinition(id=directive_id, **body.model_dump())


@router.post("/directives/{directive_id}")
async def create_directive(
    directive_id: str, body: DirectiveBody, service=Depends(query_service)
):
    return asdict(
        await service.create_directive(_directive_definition(directive_id, body))
    )


@router.put("/directives/{directive_id}")
async def update_directive(
    directive_id: str, body: DirectiveBody, service=Depends(query_service)
):
    try:
        row = await service.update_directive(
            directive_id, _directive_definition(directive_id, body)
        )
    except KeyError as error:
        raise HTTPException(404, "directive not found") from error
    return asdict(row)


@router.delete("/directives/{directive_id}")
async def delete_directive(directive_id: str, service=Depends(query_service)):
    return {"deleted": await service.delete_directive(directive_id)}


@router.get("/policy")
async def get_policy(service=Depends(query_service)):
    snapshot = await service.get_memory_policy()
    return {
        "bank_id": snapshot.bank_id,
        "version": snapshot.version,
        "policy": snapshot.policy.model_dump(mode="json"),
    }


@router.put("/policy")
async def update_policy(body: PolicyBody, service=Depends(query_service)):
    try:
        snapshot = await service.update_memory_policy(
            body.policy, expected_version=body.expected_version
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {
        "bank_id": snapshot.bank_id,
        "version": snapshot.version,
        "policy": snapshot.policy.model_dump(mode="json"),
    }
