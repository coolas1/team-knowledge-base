"""MemoryStateEnricher: DocumentRef/dict enrichment from memory state."""
from src.engine.interface import DocumentRef
from src.engine.hindsight_components.enrich import MemoryStateEnricher
from src.engine.hindsight_components.types import DocumentMemoryState


class FakeReader:
    def __init__(self, states: dict[str, DocumentMemoryState]):
        self._states = states

    async def document_state(self, document_id):
        return self._states.get(document_id)

    async def document_states(self, document_ids):
        return {i: s for i, s in self._states.items() if i in document_ids}


class FailingReader:
    async def document_state(self, document_id):
        raise RuntimeError("db down")

    async def document_states(self, document_ids):
        raise RuntimeError("db down")


def _ref(status: str = "completed") -> DocumentRef:
    return DocumentRef(id="d1", title="t", file_type="md", status=status)


async def test_enrich_ref_completed_with_state():
    state = DocumentMemoryState(
        document_id="d1", status="ready", memory_count=3, link_count=2
    )
    enricher = MemoryStateEnricher(FakeReader({"d1": state}))
    ref = _ref()
    await enricher.enrich_ref(ref)
    assert ref.memory_status == "ready"
    assert ref.memory_count == 3
    assert ref.memory_link_count == 2


async def test_enrich_ref_pending_document_short_circuits():
    enricher = MemoryStateEnricher(FakeReader({}))
    ref = _ref(status="processing")
    await enricher.enrich_ref(ref)
    assert ref.memory_status == "pending"


async def test_enrich_ref_missing_state():
    enricher = MemoryStateEnricher(FakeReader({}))
    ref = _ref()
    await enricher.enrich_ref(ref)
    assert ref.memory_status == "missing"
    assert ref.memory_count == 0


async def test_safe_state_failure_isolated():
    enricher = MemoryStateEnricher(FailingReader())
    state = await enricher.safe_state("d1")
    assert state is not None and state.status == "unavailable"


async def test_enrich_dict():
    state = DocumentMemoryState(document_id="d1", status="ready", memory_count=1)
    enricher = MemoryStateEnricher(FakeReader({"d1": state}))
    doc = {"id": "d1", "status": "completed"}
    await enricher.enrich_dict(doc)
    assert doc["memory_status"] == "ready"
