from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from src.engine.components.store.models import Document
from src.engine.components.store.postgres import async_session_factory, init_db
from src.engine.config import EngineConfig, build_engine
from src.engine.hindsight_components.conversation_queue import (
    PostgresConversationMemoryQueue,
)
from src.engine.hindsight_components.conversation_service import (
    ConversationMemoryService,
)
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.models import (
    MemoryUnit,
    ObservationEvidence,
    ObservationRecord,
)
from src.engine.interface import ConversationForgetRequest, ConversationTurn

pytestmark = pytest.mark.integration


def _completed_answer(response: httpx.Response) -> str:
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        event = json.loads(line.removeprefix("data:").strip())
        if event.get("type") == "message.completed":
            return str(event.get("answer", ""))
        if event.get("type") == "message.failed":
            raise AssertionError(event.get("error", "Pi Agent message failed"))
    raise AssertionError("Pi Agent response had no message.completed event")


async def _wait_for_completed_memory(
    client: httpx.AsyncClient,
    baseline: int,
    *,
    attempts: int = 120,
) -> None:
    for _ in range(attempts):
        response = await client.get("/health")
        payload = response.json()
        memory = payload.get("conversationMemory") or {}
        if int(memory.get("completed", 0)) > baseline:
            return
        await asyncio.sleep(1)
    raise AssertionError("conversation memory was not durably processed in time")


async def test_completed_turn_is_recalled_in_another_session_without_visible_injection():
    pi_url = os.getenv("PI_AGENT_INTEGRATION_URL", "http://127.0.0.1:8010")
    nonce = f"TKB-{uuid.uuid4().hex[:12].upper()}"
    first_id = None
    retained_session_id = None
    second_id = None
    async with httpx.AsyncClient(base_url=pi_url, timeout=180) as client:
        health = (await client.get("/health")).json()
        memory_status = health.get("conversationMemory")
        if not isinstance(memory_status, dict) or not memory_status.get("enabled"):
            pytest.fail(
                "Pi Agent conversation memory is unavailable; rebuild/restart the "
                "webapp and pi-agent services with HINDSIGHT_CONVERSATION_MEMORY_ENABLED=true "
                "and TKB_CONVERSATION_MEMORY_ENABLED=true"
            )
        baseline = int(memory_status.get("completed", 0))
        try:
            first = await client.post("/v1/sessions")
            first.raise_for_status()
            first_id = first.json()["id"]
            retained_session_id = first_id
            retained = await client.post(
                f"/v1/sessions/{first_id}/messages",
                json={
                    "message": (
                        f"Team exercise note: the shared project code is {nonce}. "
                        "Please confirm receipt of this note in one sentence."
                    )
                },
            )
            retained.raise_for_status()
            assert _completed_answer(retained).strip()
            await _wait_for_completed_memory(client, baseline)

            deleted = await client.delete(f"/v1/sessions/{first_id}")
            deleted.raise_for_status()
            first_id = None

            second = await client.post("/v1/sessions")
            second.raise_for_status()
            second_id = second.json()["id"]
            recalled = await client.post(
                f"/v1/sessions/{second_id}/messages",
                json={
                    "message": (
                        "What exact shared team code did I ask you to remember earlier? "
                        "Use the conversation memory as evidence and reply with only that code."
                    )
                },
            )
            recalled.raise_for_status()
            assert nonce in _completed_answer(recalled)

            detail = await client.get(f"/v1/sessions/{second_id}")
            detail.raise_for_status()
            messages = detail.json()["messages"]
            assert all(message["role"] in {"user", "assistant"} for message in messages)
            assert all(
                "<untrusted_conversation_memory>" not in message["text"]
                for message in messages
            )
        finally:
            if first_id:
                await client.delete(f"/v1/sessions/{first_id}")
            if retained_session_id:
                await client.delete(f"/v1/sessions/{retained_session_id}/memory")
            if second_id:
                await client.delete(f"/v1/sessions/{second_id}/memory")
                await client.delete(f"/v1/sessions/{second_id}")


class _NoRecall:
    async def recall(self, *_args, **_kwargs):
        raise AssertionError("recall is not used by this queue integration test")


async def test_queue_idempotency_file_hiding_and_targeted_forget():
    await init_db()
    run_id = uuid.uuid4().hex
    session_a = f"integration-a-{run_id}"
    session_b = f"integration-b-{run_id}"
    public_id = uuid.uuid4()
    queue = PostgresConversationMemoryQueue()
    repository = PostgresMemoryRepository()
    service = ConversationMemoryService(queue, _NoRecall(), repository)
    backend = build_engine(
        EngineConfig(impl="graphrag", config_dir=Path("config/engine/graphrag"))
    )

    try:
        first = await service.enqueue_conversation_turn(
            ConversationTurn(session_a, "turn-1", "Remember alpha", "Acknowledged")
        )
        duplicate = await service.enqueue_conversation_turn(
            ConversationTurn(session_a, "turn-1", "Remember alpha", "Acknowledged")
        )
        other = await service.enqueue_conversation_turn(
            ConversationTurn(session_b, "turn-1", "Remember beta", "Acknowledged")
        )
        assert first.document_id == duplicate.document_id
        assert await queue.session_document_ids(session_a) == [first.document_id]

        async with async_session_factory() as session:
            session.add(
                Document(
                    id=public_id,
                    title=f"integration-public-{run_id}.md",
                    file_type="markdown",
                    raw_text="public file content",
                    overview="",
                    status="indexed",
                )
            )
            await session.commit()
        listed = await backend.list_documents(page=1, page_size=500)
        listed_ids = {item["id"] for item in listed["items"]}
        assert str(public_id) in listed_ids
        assert first.document_id not in listed_ids
        assert other.document_id not in listed_ids
        assert await backend.get_document(first.document_id) is None

        forgotten = await service.forget_conversation_memory(
            ConversationForgetRequest(session_id=session_a)
        )
        assert forgotten.deleted_documents == 1
        assert await queue.session_document_ids(session_a) == []
        assert await queue.session_document_ids(session_b) == [other.document_id]
        async with async_session_factory() as session:
            assert await session.get(Document, public_id) is not None
    finally:
        await service.forget_conversation_memory(
            ConversationForgetRequest(session_id=session_a)
        )
        await service.forget_conversation_memory(
            ConversationForgetRequest(session_id=session_b)
        )
        async with async_session_factory() as session:
            await session.execute(delete(Document).where(Document.id == public_id))
            await session.commit()


async def test_delete_reanchors_observation_without_position_collision():
    await init_db()
    source_id, replacement_id = uuid.uuid4(), uuid.uuid4()
    source_fact_id, replacement_fact_id = uuid.uuid4(), uuid.uuid4()
    observation_id, occupied_id = uuid.uuid4(), uuid.uuid4()
    repository = PostgresMemoryRepository()

    try:
        async with async_session_factory() as session, session.begin():
            session.add_all(
                [
                    Document(
                        id=source_id,
                        title="conversation-to-delete",
                        file_type="conversation",
                        raw_text="source",
                        status="indexed",
                    ),
                    Document(
                        id=replacement_id,
                        title="remaining-conversation",
                        file_type="conversation",
                        raw_text="replacement",
                        status="indexed",
                    ),
                ]
            )
            await session.flush()
            common = {
                "chunk_index": 0,
                "memory_index": 0,
                "text": "fact",
                "source_text": "fact",
            }
            session.add_all(
                [
                    MemoryUnit(
                        id=source_fact_id,
                        document_id=source_id,
                        **common,
                    ),
                    MemoryUnit(
                        id=replacement_fact_id,
                        document_id=replacement_id,
                        **common,
                    ),
                    MemoryUnit(
                        id=observation_id,
                        document_id=source_id,
                        chunk_index=-2,
                        memory_index=3,
                        memory_type="observation",
                        text="cross-source observation",
                        source_text="cross-source observation",
                        source_memory_ids=[source_fact_id, replacement_fact_id],
                    ),
                    MemoryUnit(
                        id=occupied_id,
                        document_id=replacement_id,
                        chunk_index=-2,
                        memory_index=3,
                        memory_type="observation",
                        text="existing observation",
                        source_text="existing observation",
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ObservationRecord(
                        memory_id=observation_id,
                        normalized_text="cross-source observation",
                    ),
                    ObservationEvidence(
                        observation_id=observation_id,
                        fact_id=replacement_fact_id,
                        fact_version=1,
                    ),
                ]
            )

        await repository.delete_document(str(source_id))

        async with async_session_factory() as session:
            reanchored = await session.get(MemoryUnit, observation_id)
            assert reanchored is not None
            assert reanchored.document_id == replacement_id
            assert reanchored.chunk_index == -2
            assert reanchored.memory_index > 3
            positions = list(
                await session.scalars(
                    select(MemoryUnit.memory_index).where(
                        MemoryUnit.document_id == replacement_id,
                        MemoryUnit.chunk_index == -2,
                    )
                )
            )
            assert len(positions) == len(set(positions))
    finally:
        async with async_session_factory() as session, session.begin():
            await session.execute(
                delete(Document).where(Document.id.in_((source_id, replacement_id)))
            )
