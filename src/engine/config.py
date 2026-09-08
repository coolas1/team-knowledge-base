"""Engine config + factory: selects a KnowledgeBase implementation by name."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config.schema import AppConfig
from src.engine.interface import DocumentIndexHook, KnowledgeBase


@dataclass
class MemorySettings:
    retain_max_concurrent: int = 1


@dataclass
class IngestSettings:
    chunk_concurrency: int = 4
    doc_concurrency: int = 2


@dataclass
class EngineConfig:
    impl: str
    config_dir: Path
    index_hook: DocumentIndexHook | None = None
    ingest: IngestSettings = field(default_factory=IngestSettings)
    memory: MemorySettings | None = None


def engine_config_from_app(app: AppConfig) -> EngineConfig:
    """Map AppConfig (engine.memory flags) onto EngineConfig."""
    memory = (
        MemorySettings(retain_max_concurrent=app.engine.memory.retain_max_concurrent)
        if app.engine.memory.enabled
        else None
    )
    return EngineConfig(
        impl=app.engine.impl,
        config_dir=Path(app.engine.config),
        ingest=IngestSettings(
            chunk_concurrency=app.engine.ingest.chunk_concurrency,
            doc_concurrency=app.engine.ingest.doc_concurrency,
        ),
        memory=memory,
    )


def build_engine(config: EngineConfig) -> KnowledgeBase:
    """Build the engine implementation selected by config.impl.

    graphrag -> src.engine.graphrag.backend:build(config)
    """
    if config.impl == "graphrag":
        from src.engine.graphrag.backend import build

        return build(config)
    raise ValueError(f"unknown engine impl: {config.impl}")
