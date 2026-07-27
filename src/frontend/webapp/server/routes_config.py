"""BFF config routes: read / modify config/app.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from config.schema import AppConfig

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_PATH = Path("config/app.yaml")


@router.get("")
async def get_config():
    if not CONFIG_PATH.exists():
        return AppConfig().model_dump()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data).model_dump()


@router.put("")
async def put_config(body: dict):
    try:
        cfg = AppConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors())
    CONFIG_PATH.write_text(
        yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cfg.model_dump()
