"""AppConfig parsing for the engine.memory capability block."""

from config.schema import load_config


def test_memory_defaults_off(tmp_path):
    cfg = load_config(tmp_path / "missing.yaml")  # missing file -> defaults
    assert cfg.engine.memory.enabled is False
    assert cfg.engine.memory.graph_worker is True
    assert cfg.engine.memory.retain_max_concurrent == 1
    assert cfg.engine.memory.retain_chunk_concurrency == 4
    assert cfg.engine.memory.entity_resolution_max_concurrent == 8
    assert cfg.engine.memory.entity_resolution_timeout_seconds == 60
    assert cfg.engine.memory.consolidation_llm_timeout_seconds == 300
    assert cfg.engine.memory.consolidation_lease_seconds == 720


def test_memory_block_parses(tmp_path):
    p = tmp_path / "app.yaml"
    p.write_text(
        "engine:\n"
        "  impl: graphrag\n"
        "  memory:\n"
        "    enabled: true\n"
        "    graph_worker: false\n"
        "    retain_max_concurrent: 2\n"
        "    retain_chunk_concurrency: 3\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.engine.impl == "graphrag"
    assert cfg.engine.memory.enabled is True
    assert cfg.engine.memory.graph_worker is False
    assert cfg.engine.memory.retain_max_concurrent == 2
    assert cfg.engine.memory.retain_chunk_concurrency == 3


def test_host_axis_rejected():
    import pytest
    from pydantic import ValidationError

    from config.schema import AppConfig

    with pytest.raises(ValidationError):
        AppConfig.model_validate({"host": {"impl": "webapp"}})


def test_hindsight_axis_rejected():
    import pytest
    from pydantic import ValidationError

    from config.schema import AppConfig

    with pytest.raises(ValidationError):
        AppConfig.model_validate({"hindsight": {"enabled": True}})
