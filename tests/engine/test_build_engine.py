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


def test_engine_config_from_app_memory_off():
    from config.schema import AppConfig
    from src.engine.config import engine_config_from_app

    cfg = engine_config_from_app(AppConfig())
    assert cfg.impl == "graphrag"
    assert cfg.memory is None


def test_engine_config_from_app_memory_on():
    from config.schema import AppConfig, EngineCfg, MemoryCfg
    from src.engine.config import engine_config_from_app

    app = AppConfig(engine=EngineCfg(memory=MemoryCfg(enabled=True)))
    cfg = engine_config_from_app(app)
    assert cfg.memory is not None
    assert cfg.memory.retain_max_concurrent == 1


def test_build_memory_wiring(monkeypatch):
    import src.engine.graphrag.backend as backend_mod
    import src.engine.hindsight_components.repository as repo_mod
    from src.engine.config import MemorySettings

    built = {}

    class FakeRepo:
        def __init__(self, **kwargs):
            built["repository_options"] = kwargs

    def fake_hook(*, max_concurrent, repository):
        built["hook"] = (max_concurrent, repository)
        return object()

    monkeypatch.setattr(backend_mod, "Neo4jClient", lambda: None)
    monkeypatch.setattr(backend_mod, "Analyzer", lambda schema_path: None)
    monkeypatch.setattr(
        backend_mod,
        "Pipeline",
        lambda neo4j, analyzer, index_hook: built.update(pipeline_hook=index_hook),
    )
    monkeypatch.setattr(repo_mod, "PostgresMemoryRepository", FakeRepo)
    monkeypatch.setattr(backend_mod, "build_retain_hook", fake_hook)

    cfg = EngineConfig(
        impl="graphrag",
        config_dir=Path("config/engine/graphrag"),
        memory=MemorySettings(retain_max_concurrent=2),
    )
    backend_mod.build(cfg)
    assert built["hook"][0] == 2
    assert built["repository_options"] == {"consolidation_enabled": False}
    assert isinstance(built["hook"][1], FakeRepo)
    assert built["pipeline_hook"] is not None
