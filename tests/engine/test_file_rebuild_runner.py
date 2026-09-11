from types import SimpleNamespace
import uuid

import pytest

from src.engine.hindsight_components.consolidation import (
    ConsolidationOptions,
    ConsolidationReadSet,
    ConsolidationWorker,
    PostgresConsolidationRepository,
)
from src.engine.hindsight_components.file_rebuild_runner import (
    RebuildConsolidationRepository,
)


@pytest.mark.parametrize("active_evidence", [0, 1])
async def test_retired_tombstones_do_not_repeat_synthesis(monkeypatch, active_evidence):
    fact, document = str(uuid.uuid4()), str(uuid.uuid4())
    counts = iter([0, 0, active_evidence])

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def scalar(self, _statement):
            return next(counts)

    async def original(*_):
        # The ordinary read set includes existing observation evidence even
        # when the claimed events contain only deletions.
        return ConsolidationReadSet(
            {}, {"existing-evidence": object()}, {}, {}, (fact,)
        )

    monkeypatch.setattr(PostgresConsolidationRepository, "read_set", original)
    repository = RebuildConsolidationRepository(
        Session,
        {
            "bank_id": "a",
            "targets": [{"document_id": document, "fact_versions": {fact: 1}}],
        },
        ["[]"],
    )
    claim = SimpleNamespace(
        bank_id="a", scope_key="[]", processed_through=0, claimed_through=4
    )
    if active_evidence:
        with pytest.raises(ValueError, match="unexpectedly active"):
            await repository.read_set(claim, ConsolidationOptions())
    else:
        result = await repository.read_set(claim, ConsolidationOptions())
        assert not result.facts and not result.deleted_fact_ids


def test_action_limit_does_not_shrink_event_batch():
    options = ConsolidationOptions(batch_size=64, action_limit=4)
    prompt = ConsolidationWorker(None, None, options)._prompt(
        ConsolidationReadSet({}, {}, {}, {}, ())
    )
    assert options.batch_size == 64 and "at most 4 actions" in prompt
    with pytest.raises(ValueError, match="action limit"):
        ConsolidationOptions(action_limit=0)
