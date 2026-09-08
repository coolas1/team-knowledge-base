"""Validated per-bank policy snapshots and batch dependencies."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: bool = False
    reliable_retention: bool = False
    entity_resolution: bool = False
    consolidation: bool = False
    evidence_retrieval: bool = False
    mental_models: bool = False
    adaptive_reflect: bool = False

    @model_validator(mode="after")
    def dependencies(self):
        if self.entity_resolution and not self.reliable_retention:
            raise ValueError("entity_resolution requires reliable_retention")
        chain = (
            "scope",
            "reliable_retention",
            "consolidation",
            "evidence_retrieval",
            "mental_models",
            "adaptive_reflect",
        )
        for parent, child in zip(chain, chain[1:]):
            if getattr(self, child) and not getattr(self, parent):
                raise ValueError(f"{child} requires {parent}")
        return self


class ScopePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_name: str | None = None
    retain_mission: str = ""
    extraction_mode: str = "default"
    naming_strategy: str = "default"
    reflect_mission: str = ""
    max_tokens: int = Field(default=4096, ge=1, le=1_000_000)
    max_iterations: int = Field(default=8, ge=1, le=100)
    timeout_seconds: float = Field(default=60, gt=0, le=3600)
    observation_scopes: tuple[tuple[str, ...], ...] = ()
    features: MemoryFeatures = Field(default_factory=MemoryFeatures)

    @model_validator(mode="after")
    def validate_scopes(self):
        from src.engine.scope import MemoryScope

        MemoryScope(observation_scopes=self.observation_scopes)
        return self
