"""Engine config + factory: selects a KnowledgeBase implementation by name."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engine.interface import KnowledgeBase


@dataclass
class EngineConfig:
    impl: str
    config_dir: Path


def build_engine(config: EngineConfig) -> KnowledgeBase:
    """Build the engine implementation selected by config.impl.

    graphrag -> src.engine.graphrag.backend:build(config)
    """
    if config.impl == "graphrag":
        from src.engine.graphrag.backend import build

        return build(config)
    raise ValueError(f"unknown engine impl: {config.impl}")
