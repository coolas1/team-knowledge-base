from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_scale_module():
    path = Path(__file__).with_name("scale_fixture.py")
    spec = importlib.util.spec_from_file_location("retrieval_refine_scale", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scale_fixture_is_deterministic_and_production_sized():
    module = _load_scale_module()
    first = module.build_manifest(30_000)
    second = module.build_manifest(30_000)
    assert first == second
    assert first["records"] == 30_000
    assert sum(first["source_counts"].values()) == 30_000
    assert first["source_counts"]["conversation"] > first["source_counts"]["upload"]
    assert first["indexed_candidate_bound"] == 300
