from types import SimpleNamespace

import pytest

from src.engine.hindsight_components.mental_models import (
    MentalModelClaim,
    MentalModelDefinition,
    MentalModelRefreshWorker,
    apply_delta,
)


def test_definition_and_delta_are_strict():
    definition = MentalModelDefinition(
        id="project",
        name="Project",
        source_query="What is current?",
        tags=("b", "a", "a"),
    )
    assert definition.tags == ("a", "b")
    assert (
        apply_delta(
            "Status: old",
            {
                "base_version": 2,
                "operations": [{"op": "replace", "old": "old", "new": "current"}],
            },
            base_version=2,
        )
        == "Status: current"
    )
    with pytest.raises(ValueError, match="exactly once"):
        apply_delta(
            "same same",
            {
                "base_version": 2,
                "operations": [{"op": "delete", "old": "same"}],
            },
            base_version=2,
        )


@pytest.mark.asyncio
async def test_invalid_delta_falls_back_to_full_refresh():
    claim = MentalModelClaim(
        bank_id="bank",
        model_id="project",
        lease_token="lease",
        requested_watermark=4,
        base_version=1,
        name="Project",
        source_query="Status?",
        tags=("project",),
        refresh_mode="delta",
        current_summary="Old",
    )

    class Repository:
        published = None

        async def claim(self, _options):
            return claim

        async def publish(self, _claim, **kwargs):
            self.published = kwargs
            return kwargs

        async def fail(self, _claim, _error):
            raise AssertionError("valid full fallback must publish")

    class Recall:
        async def recall(self, *_args, **_kwargs):
            return SimpleNamespace(
                results=[
                    SimpleNamespace(
                        id="00000000-0000-0000-0000-000000000001",
                        text="Current evidence",
                        memory_type="world",
                        metadata={"memory_version": 3},
                    )
                ]
            )

    class Providers:
        calls = 0

        async def json_with_usage(self, _system, _user):
            self.calls += 1
            if self.calls == 1:
                return {"base_version": 1, "operations": []}, {}
            return {"summary": "Current model"}, {"total_tokens": 7}

    repository = Repository()
    worker = MentalModelRefreshWorker(repository, lambda _scope: Recall(), Providers())
    result = await worker.run_once()

    assert result["summary"] == "Current model"
    assert repository.published["mode"] == "full"
    assert repository.published["source_versions"] == {
        "00000000-0000-0000-0000-000000000001": 3
    }
