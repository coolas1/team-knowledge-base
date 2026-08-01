"""Unified Hindsight recall/reflect route."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.agent.interface import EngineClient
from src.frontend.webapp.server import deps

router = APIRouter(tags=["query"])


class KnowledgeQueryBody(BaseModel):
    query: str = Field(min_length=1)
    strategy: Literal["auto", "recall", "reflect"] = "auto"
    mode: Literal["fast", "deep"] = "deep"
    top_k: int = Field(default=10, ge=1)
    needs_answer: bool = True

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query cannot be empty")
        return value


@router.post("/query")
async def query_knowledge(
    body: KnowledgeQueryBody,
    engine: EngineClient = Depends(deps.get_engine),
):
    try:
        return await engine.query(
            body.query,
            strategy=body.strategy,
            mode=body.mode,
            top_k=body.top_k,
            needs_answer=body.needs_answer,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
