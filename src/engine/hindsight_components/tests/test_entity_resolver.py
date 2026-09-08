import asyncio
import json

import pytest

from src.engine.hindsight_components.entity_resolver import (
    EntityCandidate,
    EntityResolver,
)


class Candidates:
    def __init__(self):
        self.names = None

    async def entity_candidates(self, names, *, limit):
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
