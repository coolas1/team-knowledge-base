from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.engine.hindsight_components.consolidation import (
    ConsolidationClaim,
    ConsolidationOptions,
    PostgresConsolidationRepository,
    ConsolidationReadSet,
    ConsolidationWorker,
    retry_batch_size,
)
from src.engine.hindsight_components.consolidation_actions import (
    ConsolidationAction,
    EvidenceVersion,
    ObservationVersion,
    ValidatedAction,
    validate_actions,
)
from src.engine.hindsight_components.models import MemoryUnit
from src.engine.scope import MemoryScope


def evidence(identity: str, *, bank: str = "a", tags=("user:1",)):
    return EvidenceVersion(identity, 1, bank, tags)


def test_retry_batch_size_halves_after_each_failure():
    assert [retry_batch_size(32, attempts) for attempts in range(8)] == [
        32,
        16,
        8,
        4,
        2,
        1,
        1,
        1,
    ]


def test_action_validation_is_scoped_and_unions_repeated_update_evidence():
    observation_id = str(uuid.uuid4())
    facts = {name: evidence(name) for name in ("old", "new-a", "new-b")}
    observations = {
        observation_id: ObservationVersion(
            observation_id, 2, "a", ("user:1",), "active", ("old",)
        )
    }
    result = validate_actions(
        {
            "actions": [
                {
                    "action": "update",
                    "observation_id": observation_id,
                    "text": "latest",
                    "source_fact_ids": ["new-a"],
                    "change": "change",
                },
                {
                    "action": "update",
                    "observation_id": observation_id,
                    "text": "final",
                    "source_fact_ids": ["new-b"],
                    "change": "conflict",
                },
            ]
        },
        scope=MemoryScope(bank_id="a", observation_scopes=(("user:1",),)),
        write_scope=("user:1",),
        facts=facts,
        observations=observations,
    )
    assert len(result) == 1
    assert result[0].action.text == "final"
    assert result[0].action.change == "conflict"
    assert result[0].action.source_fact_ids == ["new-a", "new-b", "old"]
    assert result[0].expected_version == 2


@pytest.mark.parametrize(
    "payload,facts,observations,error",
    [
        (
            {"actions": [{"action": "create", "text": "x", "source_fact_ids": ["x"]}]},
            {"x": evidence("x", bank="b")},
            {},
            "valid read set",
        ),
        (
            {
                "actions": [
                    {
                        "action": "update",
                        "observation_id": "unread",
                        "text": "x",
                        "source_fact_ids": ["x"],
                    }
                ]
            },
            {"x": evidence("x")},
            {},
            "writable read set",
        ),
    ],
)
def test_action_validation_rejects_cross_scope_or_unread_targets(
    payload, facts, observations, error
):
    with pytest.raises(ValueError, match=error):
        validate_actions(
            payload,
            scope=MemoryScope(bank_id="a", observation_scopes=(("user:1",),)),
            write_scope=("user:1",),
            facts=facts,
            observations=observations,
        )


def test_action_validation_rejects_unbounded_model_output():
    with pytest.raises(ValueError, match="action limit"):
        validate_actions(
            {
                "actions": [
                    {"action": "create", "text": str(index), "source_fact_ids": ["x"]}
                    for index in range(3)
                ]
            },
            scope=MemoryScope(bank_id="a", observation_scopes=(("user:1",),)),
            write_scope=("user:1",),
            facts={"x": evidence("x")},
            observations={},
            max_actions=2,
        )


async def test_worker_reports_failure_and_does_not_publish_invalid_model_action():
    fact_id = str(uuid.uuid4())
    claim = ConsolidationClaim("a", "[]", (), str(uuid.uuid4()), 0, 1, 1, 0, 0, 0)

    class Repository:
        failed = False
        published = False

        async def claim(self, _options):
            return claim

        async def read_set(self, *_args):
            return ConsolidationReadSet(
                {fact_id: EvidenceVersion(fact_id, 1, "a", ())},
                {},
                {},
                {},
                (),
            )

        async def publish(self, *_args, **_kwargs):
            self.published = True

        async def fail(self, _claim, _error):
            self.failed = True

    class Providers:
        async def json(self, *_args, **_kwargs):
            return {
                "actions": [
                    {
                        "action": "create",
                        "text": "invented",
                        "source_fact_ids": ["unknown"],
                    }
                ]
            }

        async def embed(self, texts):
            return [[1.0] for _ in texts]

    repository = Repository()
    with pytest.raises(ValueError, match="valid read set"):
        await ConsolidationWorker(
            repository, Providers(), ConsolidationOptions()
        ).run_once()
    assert repository.failed
    assert not repository.published


async def test_worker_repairs_one_invalid_model_plan_before_failing_job():
    fact_id = str(uuid.uuid4())
    claim = ConsolidationClaim("a", "[]", (), str(uuid.uuid4()), 0, 1, 1, 0, 0, 0)

    class Repository:
        published = False

        async def claim(self, _options):
            return claim

        async def read_set(self, *_args):
            return ConsolidationReadSet(
                {fact_id: EvidenceVersion(fact_id, 1, "a", ())}, {}, {}, {}, ()
            )

        async def publish(self, *_args, **_kwargs):
            self.published = True
            return None

        async def fail(self, *_args):
            raise AssertionError("a repaired plan must not fail")

    class Providers:
        calls = 0

        async def json(self, *_args, **_kwargs):
            self.calls += 1
            source_id = "unknown" if self.calls == 1 else fact_id
            return {
                "actions": [
                    {
                        "action": "create",
                        "text": "verified",
                        "source_fact_ids": [source_id],
                    }
                ]
            }

        async def embed(self, texts):
            return [[1.0] for _ in texts]

    repository = Repository()
    providers = Providers()
    await ConsolidationWorker(repository, providers, ConsolidationOptions()).run_once()
    assert providers.calls == 2
    assert repository.published


async def test_worker_accounts_provider_usage_and_configured_cost():
    fact_id = str(uuid.uuid4())
    claim = ConsolidationClaim("a", "[]", (), str(uuid.uuid4()), 0, 1, 1, 0, 0, 0)

    class Repository:
        published = None

        async def claim(self, _options):
            return claim

        async def read_set(self, *_args):
            return ConsolidationReadSet(
                {fact_id: EvidenceVersion(fact_id, 1, "a", ())}, {}, {}, {}, ()
            )

        async def publish(self, *_args, **kwargs):
            self.published = kwargs
            return None

        async def fail(self, *_args):
            raise AssertionError("valid result must not fail")

    class Providers:
        async def json_with_usage(self, *_args, **_kwargs):
            return {"actions": []}, {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            }

        async def embed(self, _texts):
            return []

    repository = Repository()
    await ConsolidationWorker(
        repository,
        Providers(),
        ConsolidationOptions(
            input_cost_usd_per_million=2,
            output_cost_usd_per_million=8,
        ),
    ).run_once()

    assert repository.published["tokens_used"] == 120
    assert repository.published["cost_microusd"] == 360


@pytest.mark.parametrize("equivalent,expected", [(True, "update"), (False, "create")])
async def test_semantic_dedup_requires_both_threshold_and_equivalence(
    equivalent, expected
):
    fact_id = str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    action = ConsolidationAction(
        action="create", text="User likes brief replies", source_fact_ids=[fact_id]
    )
    validated = (ValidatedAction(action, None, ((fact_id, 1),)),)
    read_set = ConsolidationReadSet(
        facts={
            fact_id: EvidenceVersion(fact_id, 1, "a", ()),
        },
        fact_rows={},
        observations={
            observation_id: ObservationVersion(
                observation_id, 3, "a", (), "active", (fact_id,)
            )
        },
        observation_rows={
            observation_id: MemoryUnit(
                id=uuid.UUID(observation_id),
                document_id=uuid.uuid4(),
                chunk_index=-2,
                memory_index=1,
                memory_type="observation",
                text="The user prefers concise answers",
                source_text="",
                embedding=[1.0, 0.0],
                state="active",
            )
        },
        deleted_fact_ids=(),
    )

    class Providers:
        async def embed(self, _texts):
            return [[1.0, 0.0]]

        async def json(self, *_args, **_kwargs):
            return {"equivalent": equivalent}

    worker = ConsolidationWorker(
        None,
        Providers(),
        ConsolidationOptions(semantic_threshold=0.8),
    )
    result = await worker._semantic_coalesce(validated, read_set)
    assert result[0].action.action == expected
    if equivalent:
        assert result[0].action.observation_id == observation_id
        assert result[0].expected_version == 3


async def test_semantic_dedup_batches_candidate_verdicts():
    fact_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    observation_id = str(uuid.uuid4())
    actions = tuple(
        ValidatedAction(
            ConsolidationAction(
                action="create", text=f"proposal {index}", source_fact_ids=[fact_id]
            ),
            None,
            ((fact_id, 1),),
        )
        for index, fact_id in enumerate(fact_ids)
    )
    read_set = ConsolidationReadSet(
        facts={item: EvidenceVersion(item, 1, "a", ()) for item in fact_ids},
        fact_rows={},
        observations={
            observation_id: ObservationVersion(
                observation_id, 1, "a", (), "active", tuple(fact_ids)
            )
        },
        observation_rows={
            observation_id: MemoryUnit(
                id=uuid.UUID(observation_id),
                text="existing",
                state="active",
                embedding=[1.0, 0.0],
            )
        },
        deleted_fact_ids=(),
    )

    class Providers:
        json_calls = 0
        embed_calls = 0

        async def embed(self, texts):
            self.embed_calls += 1
            return [[1.0, 0.0] for _ in texts]

        async def json(self, *_args, **_kwargs):
            self.json_calls += 1
            return {"equivalent_indices": [0, 1]}

    providers = Providers()
    result = await ConsolidationWorker(
        None, providers, ConsolidationOptions(semantic_threshold=0.8)
    )._semantic_coalesce(actions, read_set)
    assert providers.embed_calls == 1
    assert providers.json_calls == 1
    assert [item.action.action for item in result] == ["update", "update"]


@pytest.mark.parametrize(
    "provider_error",
    [TimeoutError(), ValueError("empty model content")],
    ids=["timeout", "invalid-json"],
)
async def test_semantic_dedup_provider_failure_keeps_validated_actions(
    provider_error, caplog
):
    fact_id = str(uuid.uuid4())
    observation_id = str(uuid.uuid4())
    actions = (
        ValidatedAction(
            ConsolidationAction(
                action="create",
                text="User likes brief replies",
                source_fact_ids=[fact_id],
            ),
            None,
            ((fact_id, 1),),
        ),
    )
    read_set = ConsolidationReadSet(
        facts={fact_id: EvidenceVersion(fact_id, 1, "a", ())},
        fact_rows={},
        observations={
            observation_id: ObservationVersion(
                observation_id, 1, "a", (), "active", (fact_id,)
            )
        },
        observation_rows={
            observation_id: MemoryUnit(
                id=uuid.UUID(observation_id),
                text="The user prefers concise answers",
                state="active",
                embedding=[1.0, 0.0],
            )
        },
        deleted_fact_ids=(),
    )

    class Providers:
        async def embed(self, _texts):
            return [[1.0, 0.0]]

        async def json(self, *_args, **_kwargs):
            raise provider_error

    result = await ConsolidationWorker(
        None, Providers(), ConsolidationOptions(semantic_threshold=0.8)
    )._semantic_coalesce(actions, read_set)

    assert result == actions
    assert "semantic consolidation deduplication skipped" in caplog.text


async def test_completed_source_stage_waits_for_its_last_event():
    completed_id = uuid.uuid4()
    pending_id = uuid.uuid4()
    document_state = SimpleNamespace(stage_results={"consolidate": "queued"})
    conversation_source = SimpleNamespace(
        stage_results={"delivery": "accepted", "consolidate": "queued"}
    )

    class Session:
        def __init__(self):
            self.results = iter(
                [
                    [completed_id, pending_id],
                    [pending_id],
                    [document_state],
                    [conversation_source],
                ]
            )

        async def scalars(self, _statement):
            return next(self.results)

    await PostgresConsolidationRepository._mark_completed_source_stages(
        Session(),
        ConsolidationClaim(
            bank_id="default-team",
            scope_key="[]",
            write_scope=(),
            lease_token=str(uuid.uuid4()),
            processed_through=10,
            claimed_through=20,
            pending_through=30,
            iterations=0,
            tokens_used=0,
            cost_microusd=0,
        ),
    )

    assert document_state.stage_results["consolidate"] == "success"
    assert conversation_source.stage_results["consolidate"] == "success"


async def test_semantic_dedup_merges_update_target_and_preserves_both_histories():
    fact_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    observation_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    action = ConsolidationAction(
        action="update",
        observation_id=observation_ids[0],
        text="User likes brief replies",
        source_fact_ids=fact_ids,
    )
    validated = (ValidatedAction(action, 2, tuple((item, 1) for item in fact_ids)),)
    observations = {
        observation_ids[0]: ObservationVersion(
            observation_ids[0], 2, "a", (), "active", (fact_ids[0],)
        ),
        observation_ids[1]: ObservationVersion(
            observation_ids[1], 4, "a", (), "active", (fact_ids[1],)
        ),
    }
    rows = {
        identity: MemoryUnit(
            id=uuid.UUID(identity),
            document_id=uuid.uuid4(),
            chunk_index=-2,
            memory_index=index,
            memory_type="observation",
            text="The user prefers concise answers",
            source_text="",
            embedding=[1.0, 0.0],
            state="active",
        )
        for index, identity in enumerate(observation_ids)
    }

    class Providers:
        async def embed(self, _texts):
            return [[1.0, 0.0]]

        async def json(self, *_args, **_kwargs):
            return {"equivalent": True}

    read_set = ConsolidationReadSet(
        facts={item: EvidenceVersion(item, 1, "a", ()) for item in fact_ids},
        fact_rows={},
        observations=observations,
        observation_rows=rows,
        deleted_fact_ids=(),
    )
    result = await ConsolidationWorker(
        None, Providers(), ConsolidationOptions(semantic_threshold=0.8)
    )._semantic_coalesce(validated, read_set)
    assert [(item.action.action, item.action.observation_id) for item in result] == [
        ("update", observation_ids[1]),
        ("delete", observation_ids[0]),
    ]
    assert result[0].expected_version == 4
    assert set(result[0].action.source_fact_ids) == set(fact_ids)


def test_exact_dedup_applies_to_update_even_when_semantic_dedup_is_disabled():
    fact_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    observation_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    read_set = ConsolidationReadSet(
        facts={item: EvidenceVersion(item, 1, "a", ()) for item in fact_ids},
        fact_rows={},
        observations={
            observation_ids[0]: ObservationVersion(
                observation_ids[0], 1, "a", (), "active", (fact_ids[0],)
            ),
            observation_ids[1]: ObservationVersion(
                observation_ids[1], 2, "a", (), "active", (fact_ids[1],)
            ),
        },
        observation_rows={
            observation_ids[0]: MemoryUnit(
                id=uuid.UUID(observation_ids[0]), text="old", state="active"
            ),
            observation_ids[1]: MemoryUnit(
                id=uuid.UUID(observation_ids[1]), text="same text", state="active"
            ),
        },
        deleted_fact_ids=(),
    )
    action = ConsolidationAction(
        action="update",
        observation_id=observation_ids[0],
        text=" same   text ",
        source_fact_ids=[fact_ids[0]],
    )
    result = ConsolidationWorker._exact_coalesce(
        (ValidatedAction(action, 1, ((fact_ids[0], 1),)),), read_set
    )
    assert [(item.action.action, item.action.observation_id) for item in result] == [
        ("update", observation_ids[1]),
        ("delete", observation_ids[0]),
    ]
    assert set(result[0].action.source_fact_ids) == set(fact_ids)
