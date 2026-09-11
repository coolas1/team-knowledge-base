import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.engine.hindsight_components.entity_resolver import (
    EntityCandidate,
    EntityResolver,
    resolve_plan_entities,
)
from src.engine.hindsight_components.types import MemoryDraft, RetainPlan


class Candidates:
    def __init__(self):
        self.names = None

    async def entity_candidates(self, names, *, limit, exclude_document_id=None):
        self.names = names
        return [
            EntityCandidate(
                "alice-1", "Alice Chen", ("AC",), ("Alice Chen is the Acme engineer.",)
            )
        ]


class Provider:
    def __init__(self, payload):
        self.payload = payload
        self.input = None

    async def json(self, system, user):
        self.input = json.loads(user)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


async def test_alias_candidates_require_contextual_confirmation():
    candidates = Candidates()
    provider = Provider({"decision": "match", "entity_id": "alice-1"})
    result = await EntityResolver(candidates, provider).resolve(
        "AC", "Acme engineer AC", aliases=("Alice Chen",)
    )
    assert set(candidates.names) == {"ac", "alice chen"}
    assert result.entity_id == "alice-1"
    assert provider.input["candidates"][0]["evidence"] == [
        "Alice Chen is the Acme engineer."
    ]


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"decision": "distinct", "entity_id": None}, "new"),
        ({"decision": "unknown", "entity_id": None}, "unresolved"),
        ({"decision": "match", "entity_id": "hidden-candidate"}, "degraded"),
        ({"decision": "unknown", "entity_id": "alice-1"}, "degraded"),
        ({}, "degraded"),
        (TimeoutError("provider secret"), "degraded"),
    ],
)
async def test_same_name_or_invalid_decision_never_forces_merge(payload, status):
    result = await EntityResolver(Candidates(), Provider(payload)).resolve(
        "Alice Chen", "Alice Chen is a different hospital doctor."
    )
    assert result.entity_id is None
    assert result.status == status


async def test_resolver_enforces_deadline_even_when_provider_has_no_timeout():
    class Slow(Provider):
        async def json(self, system, user):
            await asyncio.Event().wait()

    result = await EntityResolver(Candidates(), Slow(None), timeout=0.01).resolve(
        "Alice", "context"
    )
    assert result.status == "degraded"


async def test_plan_groups_repeated_entities_and_bounds_candidate_queries():
    class EmptyCandidates:
        scope = SimpleNamespace(bank_id="default-team")

        def __init__(self):
            self.calls = 0
            self.active = 0
            self.peak = 0

        async def entity_candidates(self, names, *, limit, exclude_document_id=None):
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return []

    def memory(name: str) -> MemoryDraft:
        return MemoryDraft(
            id=str(uuid4()),
            document_id="document-1",
            chunk_index=0,
            memory_index=1,
            memory_type="world",
            text=f"{name} appears",
            source_text=f"source for {name}",
            context="test",
            embedding=[1.0, 0.0],
            entities=[name],
        )

    repository = EmptyCandidates()
    alice_one = memory("Alice")
    alice_two = memory("Alice")
    bob = memory("Bob")
    plan = RetainPlan(
        document_id="document-1",
        title="test",
        file_type="text",
        source_type="upload",
        memories=[alice_one, alice_two, bob],
        links=[],
    )

    status = await resolve_plan_entities(
        plan, repository, Provider({}), max_concurrent=2
    )

    assert status == "success"
    assert repository.calls == 2
    assert repository.peak == 2
    assert (
        alice_one.metadata["resolved_entities"][0]["id"]
        == (alice_two.metadata["resolved_entities"][0]["id"])
    )
    assert (
        alice_one.metadata["resolved_entities"][0]["id"]
        != (bob.metadata["resolved_entities"][0]["id"])
    )
