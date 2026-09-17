"""BFF config routes: read the effective config, write the runtime layer.

读的是三层解析后的有效配置;写只写运行时层 config/app.runtime.yaml,
已提交的 config/app.yaml 运行期绝不被写——否则一次 UI 改动就会改掉
同一镜像下所有部署继承的默认值。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from config.schema import AppConfig, load_config_with_sources, runtime_config_path

router = APIRouter(prefix="/config", tags=["config"])

CONFIG_PATH = Path("config/app.yaml")


@router.get("")
async def get_config():
    """有效配置,外加每个键的来源层(default/app.yaml/env/runtime)。"""
    cfg, sources = load_config_with_sources(CONFIG_PATH)
    return {**cfg.model_dump(), "sources": sources}


@router.put("")
async def put_config(body: dict):
    try:
        cfg = AppConfig.model_validate(body)
    except ValidationError as e:
        raise HTTPException(422, e.errors())
    path = runtime_config_path(CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return cfg.model_dump()
