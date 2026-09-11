import pytest

from config.schema import AppConfig, MemoryCfg
from src.engine.scope_policy import MemoryFeatures, ScopePolicy


def test_existing_configuration_keeps_new_features_disabled():
    assert not AppConfig().engine.memory.features.scope
    assert not MemoryCfg(enabled=True).features.adaptive_reflect
    assert ScopePolicy.model_validate({}).max_tokens == 4096


@pytest.mark.parametrize(
    "feature",
    [
        "reliable_retention",
        "entity_resolution",
        "consolidation",
        "evidence_retrieval",
        "mental_models",
        "adaptive_reflect",
    ],
)
def test_missing_preceding_batch_is_rejected(feature):
    with pytest.raises(ValueError, match="requires"):
        MemoryFeatures.model_validate({feature: True})


def test_feature_chain_requires_memory_and_validates_policy_budgets():
    features = dict.fromkeys(MemoryFeatures.model_fields, True)
    assert MemoryFeatures(**features).adaptive_reflect
    with pytest.raises(ValueError, match="memory.enabled"):
        MemoryCfg(features=MemoryFeatures(**features))
    for invalid in (
        {"max_tokens": 0},
        {"timeout_seconds": -1},
        {"unknown": True},
        {"observation_scopes": [[""]]},
    ):
        with pytest.raises(ValueError):
            ScopePolicy.model_validate(invalid)
