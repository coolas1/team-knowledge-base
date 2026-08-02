from pathlib import Path

import pytest

from src.engine.config import EngineConfig, build_engine


def test_engine_config_defaults():
    cfg = EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    assert cfg.impl == "graphrag"


def test_build_engine_unknown_impl_raises():
    cfg = EngineConfig(impl="nope", config_dir=Path("config/engine/graphrag"))
    with pytest.raises(ValueError, match="unknown engine impl"):
        build_engine(cfg)
