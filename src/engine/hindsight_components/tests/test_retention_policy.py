from __future__ import annotations

import pytest

from src.engine.hindsight_components.retention_policy import RetentionPolicy


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我偏好简洁的回答", ("preference",)),
        ("We decided to ship on Friday", ("decision",)),
        ("I will review this tomorrow", ("commitment",)),
        ("現在この設定を使っています", ("state",)),
        ("Thanks", ()),
        ("Explain this document", ()),
    ],
)
def test_policy_classifies_only_explicit_eligible_user_statements(text, expected):
    assert RetentionPolicy().classify_user_text(text) == expected


def test_policy_parses_versioned_mapping_and_rejects_unsafe_values():
    policy = RetentionPolicy.from_mapping(
        {"version": 2, "retain_user_types": ["preference", "decision"]}
    )
    assert policy.version == 2
    with pytest.raises(ValueError, match="assistant-text"):
        RetentionPolicy.from_mapping({"retain_assistant_text": True})
    with pytest.raises(ValueError, match="unknown"):
        RetentionPolicy.from_mapping({"surprise": True})
