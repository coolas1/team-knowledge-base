from __future__ import annotations

import pytest
from src.engine.scope import MemoryScope

from src.engine.hindsight_components.conversation_service import (
    ConversationMemoryService,
)
from src.engine.hindsight_components.types import (
    ConversationMemoryJob,
    ConversationMemoryQueueStats,
    RecallResult,
)
from src.engine.interface import (
    ConversationForgetRequest,
    ConversationMemoryRecallRequest,
    ConversationProposal,
    ConversationTurn,
)


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued = None
        self.cancelled = []
        self.deleted = []

    async def enqueue(self, **kwargs):
        self.enqueued = kwargs
        return ConversationMemoryJob(
            document_id="document-1",
            session_id=kwargs["session_id"],
            turn_id=kwargs["turn_id"],
            title=kwargs["title"],
            content=kwargs["content"],
            attempts=0,
            status="pending",
        )

    async def session_document_ids(self, session_id):
        return ["document-1"] if session_id == "session-1" else []

    async def cancel_session(self, session_id):
        self.cancelled.append(session_id)
        return 1

    async def delete_documents(self, document_ids):
        self.deleted.extend(document_ids)
        return len(document_ids)

    async def status_counts(self):
        return ConversationMemoryQueueStats(pending=2, failed=1)


class FakeRecall:
    def __init__(self, candidate) -> None:
        self.candidate = candidate
        self.call = None

    async def recall(self, query, **kwargs):
        self.call = (query, kwargs)
        return RecallResult(
            results=[self.candidate],
            chunks={},
            entities={},
            trace={"source_type": kwargs["source_type"]},
        )


class FakeRepository:
    def __init__(self) -> None:
        self.deleted = []
        self.purged_orphaned_observations = 0

    async def delete_document(self, document_id):
        self.deleted.append(document_id)

    async def purge_orphaned_observations(self):
        self.purged_orphaned_observations += 1
        return 1


async def test_enqueue_uses_trusted_identity_and_preserves_original_source_time():
    queue = FakeQueue()
    queue.scope = MemoryScope(subject_id="alice", agent_name="helper", policy_version=7)
    service = ConversationMemoryService(queue, FakeRecall(None), FakeRepository())
    await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="s",
            turn_id="t",
            user_text="I finished yesterday",
            assistant_text="I suggest a review",
            source_timestamp="2024-01-01T18:00:00Z",
            reference_timezone="Asia/Shanghai",
        )
    )
    assert queue.enqueued["source_context"] == {
        "source_timestamp": "2024-01-01T18:00:00+00:00",
        "reference_timezone": "Asia/Shanghai",
        "policy_version": 7,
        "agent_name": "helper",
        "speakers": {"user": "alice", "assistant": "helper"},
    }


@pytest.mark.parametrize("timestamp", ["", "yesterday", "2024-01-01T12:00:00"])
async def test_invalid_source_time_never_reaches_queue(timestamp):
    queue = FakeQueue()
    service = ConversationMemoryService(queue, FakeRecall(None), FakeRepository())
    with pytest.raises(ValueError):
        await service.enqueue_conversation_turn(
            ConversationTurn(
                session_id="s",
                turn_id="t",
                user_text="hello",
                assistant_text="hello",
                source_timestamp=timestamp,
            )
        )
    assert queue.enqueued is None


async def test_service_recalls_only_conversation_memories_with_provenance():
    from src.engine.hindsight_components.tests.fakes import candidate

    item = candidate("memory-1", "User prefers blue")
    item.source_type = "conversation"
    item.session_id = "session-1"
    item.turn_id = "turn-1"
    item.final_score = 0.8
    recall = FakeRecall(item)
    service = ConversationMemoryService(FakeQueue(), recall, FakeRepository())

    result = await service.recall_conversation_memory(
        ConversationMemoryRecallRequest(query="preferred color", top_k=3)
    )

    assert recall.call == (
        "preferred color",
        {"mode": "fast", "top_k": 3, "source_type": "conversation"},
    )
    assert result.memories[0].session_id == "session-1"
    assert result.memories[0].turn_id == "turn-1"
    assert result.memories[0].score == 0.8


async def test_service_enqueues_only_visible_user_and_assistant_text():
    queue = FakeQueue()
    service = ConversationMemoryService(queue, FakeRecall(None), FakeRepository())

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-1",
            user_text="Remember blue",
            assistant_text="Understood",
        )
    )

    assert result.status == "pending"
    assert queue.enqueued["content"] == (
        "[user]\nRemember blue\n\n[assistant]\nUnderstood"
    )


async def test_service_bounds_oversized_turn_with_truncation_marker():
    queue = FakeQueue()
    service = ConversationMemoryService(
        queue, FakeRecall(None), FakeRepository(), max_turn_chars=1000
    )

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-1",
            user_text="x" * 200_000,
            assistant_text="done",
        )
    )

    content = queue.enqueued["content"]
    assert result.status == "pending"
    assert len(content) <= 1000 + len("\n\n[truncated]")
    assert content.endswith("[truncated]")
    assert content.startswith("[user]\nx")


async def test_selective_retention_skips_generic_turn_and_never_queues_assistant_summary():
    queue = FakeQueue()
    service = ConversationMemoryService(
        queue,
        FakeRecall(None),
        FakeRepository(),
        selective_retention_enabled=True,
    )

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-1",
            user_text="Explain the policy document",
            assistant_text="The document says the production password is secret",
        )
    )

    assert result.status == "skipped_by_policy"
    assert queue.enqueued is None


async def test_selective_retention_marks_unconfirmed_preference_as_user_stated():
    queue = FakeQueue()
    service = ConversationMemoryService(
        queue,
        FakeRecall(None),
        FakeRepository(),
        selective_retention_enabled=True,
    )

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-2",
            user_text="我偏好简洁的回答",
            assistant_text="我会记住；文档内容也说明了这一点",
            derived_from_evidence_ids=("doc:2", "doc:1", "doc:2"),
        )
    )

    assert result.status == "pending"
    assert queue.enqueued["content"] == "[user]\n我偏好简洁的回答"
    assert queue.enqueued["source_context"]["retained_types"] == ("preference",)
    assert queue.enqueued["source_context"]["origin"] == "user"
    assert queue.enqueued["source_context"]["authority"] == "user_stated"
    assert queue.enqueued["source_context"]["confirmed_by_turn_id"] is None
    assert queue.enqueued["source_context"]["derived_from_evidence_ids"] == (
        "doc:1",
        "doc:2",
    )


async def test_confirmation_retains_structured_pending_proposal_as_decision():
    queue = FakeQueue()
    service = ConversationMemoryService(
        queue,
        FakeRecall(None),
        FakeRepository(),
        selective_retention_enabled=True,
    )
    proposal = ConversationProposal(
        proposal_type="decision",
        normalized_content="后续查询排除第一条结果",
        assistant_turn_id="assistant-turn-1",
        trusted_evidence_ids=("doc:2", "doc:1"),
        expires_at="2099-01-01T00:00:00Z",
    )

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-2",
            user_text="同意",
            assistant_text="已记录",
            confirmed_by_turn_id="turn-2",
            derived_from_evidence_ids=("doc:1", "doc:2"),
            confirmed_proposal=proposal,
        )
    )

    assert result.status == "pending"
    assert queue.enqueued["content"] == "[user]\n后续查询排除第一条结果"
    context = queue.enqueued["source_context"]
    assert context["retained_types"] == ("decision",)
    assert context["authority"] == "user_confirmed"
    assert context["confirmed_by_turn_id"] == "turn-2"
    assert context["derived_from_evidence_ids"] == ("doc:1", "doc:2")
    assert context["confirmed_proposal"]["assistant_turn_id"] == "assistant-turn-1"


@pytest.mark.parametrize("text", ["同意", "不同意"])
async def test_confirmation_words_without_trusted_pending_proposal_are_not_retained(text):
    queue = FakeQueue()
    service = ConversationMemoryService(
        queue,
        FakeRecall(None),
        FakeRepository(),
        selective_retention_enabled=True,
    )

    result = await service.enqueue_conversation_turn(
        ConversationTurn(
            session_id="session-1",
            turn_id="turn-2",
            user_text=text,
            assistant_text="收到",
        )
    )

    assert result.status == "skipped_by_policy"
    assert queue.enqueued is None


@pytest.mark.parametrize(
    ("confirmed_by_turn_id", "evidence_ids"),
    [("another-turn", ()), (None, ("not allowed",)), (None, ("x" * 129,))],
)
async def test_service_rejects_untrusted_retention_provenance(
    confirmed_by_turn_id, evidence_ids
):
    queue = FakeQueue()
    service = ConversationMemoryService(queue, FakeRecall(None), FakeRepository())

    with pytest.raises(ValueError):
        await service.enqueue_conversation_turn(
            ConversationTurn(
                session_id="session-1",
                turn_id="turn-1",
                user_text="Remember blue",
                assistant_text="Understood",
                confirmed_by_turn_id=confirmed_by_turn_id,
                derived_from_evidence_ids=evidence_ids,
            )
        )

    assert queue.enqueued is None


async def test_service_forgets_only_requested_session_and_reports_diagnostics():
    queue = FakeQueue()
    repository = FakeRepository()
    service = ConversationMemoryService(queue, FakeRecall(None), repository)

    forgotten = await service.forget_conversation_memory(
        ConversationForgetRequest(session_id="session-1")
    )
    diagnostics = await service.conversation_memory_diagnostics()

    assert repository.deleted == ["document-1"]
    assert repository.purged_orphaned_observations == 1
    assert queue.cancelled == ["session-1"]
    assert queue.deleted == ["document-1"]
    assert forgotten.cancelled_jobs == 1
    assert forgotten.deleted_documents == 1
    assert diagnostics.pending == 2
    assert diagnostics.failed == 1
    assert diagnostics.enabled is True
