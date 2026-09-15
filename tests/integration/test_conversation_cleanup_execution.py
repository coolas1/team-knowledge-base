from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from src.engine.components.store.models import Document, MemoryBank
from src.engine.components.store.postgres import async_session_factory, engine, init_db
from src.engine.conversation_cleanup import (
    ConversationCleanupExecutor,
    ConversationCleanupPlanner,
)
from src.engine.hindsight_components.models import (
    HindsightDocumentState,
    HindsightGraphOutbox,
    MemoryUnit,
    MentalModel,
    MentalModelRefreshJob,
    ObservationEvidence,
    ObservationRecord,
)
from src.engine.scope import MemoryScope

pytestmark = pytest.mark.integration


async def test_authorized_cleanup_retires_only_targets_and_invalidates_dependents():
    await init_db()
    bank_id = f"cleanup-{uuid.uuid4().hex[:8]}"
    other_bank = f"cleanup-other-{uuid.uuid4().hex[:8]}"
    conversation_id, upload_id, other_id = (uuid.uuid4() for _ in range(3))
    target_id, keeper_id, uploaded_memory_id, other_memory_id, observation_id = (
        uuid.uuid4() for _ in range(5)
    )
    transcript = "[user] retain this visible transcript"
    try:
        async with async_session_factory() as session, session.begin():
            session.add_all(
                [
                    MemoryBank(id=bank_id, name=bank_id),
                    MemoryBank(id=other_bank, name=other_bank),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    Document(
                        id=conversation_id,
                        bank_id=bank_id,
                        title="turn",
                        file_type="conversation",
                        raw_text=transcript,
                        status="indexed",
                    ),
                    Document(
                        id=upload_id,
                        bank_id=bank_id,
                        title="upload",
                        file_type="markdown",
                        raw_text="protected upload",
                        status="indexed",
                    ),
                    Document(
                        id=other_id,
                        bank_id=other_bank,
                        title="other",
                        file_type="conversation",
                        raw_text="unrelated scope transcript",
                        status="indexed",
                    ),
                ]
            )
            await session.flush()
            common = dict(
                chunk_index=0,
                memory_index=0,
                text="preserved audit text",
                source_text="preserved audit source",
                origin="user",
                authority="user_stated",
                lifecycle_state="current",
                state="active",
                mentioned_at=datetime.now(timezone.utc),
            )
            session.add_all(
                [
                    MemoryUnit(
                        id=target_id,
                        bank_id=bank_id,
                        document_id=conversation_id,
                        duplicate_of=keeper_id,
                        content_fingerprint="duplicate-target",
                        **common,
                    ),
                    MemoryUnit(
                        id=keeper_id,
                        bank_id=bank_id,
                        document_id=conversation_id,
                        content_fingerprint="keeper",
                        **{**common, "memory_index": 1},
                    ),
                    MemoryUnit(
                        id=uploaded_memory_id,
                        bank_id=bank_id,
                        document_id=upload_id,
                        content_fingerprint="upload",
                        **common,
                    ),
                    MemoryUnit(
                        id=other_memory_id,
                        bank_id=other_bank,
                        document_id=other_id,
                        content_fingerprint="other",
                        **common,
                    ),
                    MemoryUnit(
                        id=observation_id,
                        bank_id=bank_id,
                        document_id=conversation_id,
                        chunk_index=-1,
                        memory_index=3,
                        memory_type="observation",
                        text="derived observation",
                        source_text="derived observation",
                        source_memory_ids=[target_id],
                    ),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ObservationRecord(
                        memory_id=observation_id,
                        bank_id=bank_id,
                        normalized_text="derived observation",
                    ),
                    ObservationEvidence(
                        observation_id=observation_id,
                        fact_id=target_id,
                        fact_version=1,
                        bank_id=bank_id,
                    ),
                    MentalModel(
                        id="cleanup-derived",
                        bank_id=bank_id,
                        name="cleanup-derived",
                        description="",
                        summary="derived",
                        freshness="active",
                        source_memory_ids=[target_id],
                    ),
                    HindsightDocumentState(
                        document_id=conversation_id,
                        bank_id=bank_id,
                        extraction_cache={"sensitive": "stale"},
                    ),
                ]
            )

        scope = MemoryScope(bank_id=bank_id)
        manifest = await ConversationCleanupPlanner(
            async_session_factory, scope=scope
        ).build_manifest(reference_time=datetime.now(timezone.utc))
        assert manifest.counts["duplicate"] == 1
        result = await ConversationCleanupExecutor(
            async_session_factory, scope=scope
        ).execute(manifest)
        assert result == {"reviewed": 2, "retired": 1, "protected": 1}

        async with async_session_factory() as session:
            target = await session.get(MemoryUnit, target_id)
            assert target.state == "retired" and target.lifecycle_state == "retired"
            assert target.memory_version == 2
            assert target.text == "preserved audit text"
            assert (await session.get(MemoryUnit, keeper_id)).state == "active"
            assert (await session.get(MemoryUnit, uploaded_memory_id)).state == "active"
            assert (await session.get(MemoryUnit, other_memory_id)).state == "active"
            assert (await session.get(Document, conversation_id)).raw_text == transcript
            assert (
                await session.get(Document, upload_id)
            ).raw_text == "protected upload"
            evidence = await session.get(
                ObservationEvidence, (observation_id, target_id)
            )
            assert evidence.active is False
            assert (
                await session.get(ObservationRecord, observation_id)
            ).freshness == "stale"
            assert (await session.get(MemoryUnit, observation_id)).state == "stale"
            model = await session.get(MentalModel, ("cleanup-derived", bank_id))
            assert model.freshness == "stale"
            assert await session.get(
                MentalModelRefreshJob, (bank_id, "cleanup-derived")
            )
            state = await session.get(HindsightDocumentState, conversation_id)
            assert state.extraction_cache == {}
            assert list(
                await session.scalars(
                    select(HindsightGraphOutbox).where(
                        HindsightGraphOutbox.bank_id == bank_id
                    )
                )
            )
    finally:
        async with async_session_factory() as session, session.begin():
            await session.execute(
                delete(HindsightGraphOutbox).where(
                    HindsightGraphOutbox.bank_id.in_((bank_id, other_bank))
                )
            )
            await session.execute(
                delete(MentalModelRefreshJob).where(
                    MentalModelRefreshJob.bank_id.in_((bank_id, other_bank))
                )
            )
            await session.execute(
                delete(MentalModel).where(
                    MentalModel.bank_id.in_((bank_id, other_bank))
                )
            )
            await session.execute(delete(Document).where(Document.bank_id == bank_id))
            await session.execute(
                delete(Document).where(Document.bank_id == other_bank)
            )
            await session.execute(
                delete(MemoryBank).where(MemoryBank.id.in_((bank_id, other_bank)))
            )
        await engine.dispose()
