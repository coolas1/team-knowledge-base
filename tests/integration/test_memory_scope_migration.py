"""Scope DDL and tag semantics against an explicitly selected disposable DB."""

import os
import uuid

import pytest
from sqlalchemy import Text, insert, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.engine.components.store.models import Base, Document
from src.engine.components.store.scope import tag_predicate
from src.engine.components.store.scope_migration import TABLES, migrate_scope
from src.engine.hindsight_components.models import MemoryEntity, MemoryUnit
from src.engine.scope import TagFilter, TagGroup
from src.engine.scope import MemoryScope
from src.engine.hindsight_components.repository import PostgresMemoryRepository
from src.engine.hindsight_components.types import MemoryDraft, RetainPlan

pytestmark = pytest.mark.integration


async def test_extraction_cache_reuses_content_and_invalidates_policy(scope_database):
    from dataclasses import replace
    from src.engine.components.store.models import EMBEDDING_DIM
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.service import HindsightService
    from src.engine.hindsight_components.types import RetainInput

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessions() as session:
        session.add(Document(id=doc_id, title="cached", file_type="text"))
        await session.commit()

    class Provider:
        calls = 0
        fail = False

        async def json(self, system, user, **kwargs):
            self.calls += 1
            if self.fail:
                raise TimeoutError("injected extraction timeout")
            return {"facts": [{"text": "A grounded fact", "type": "world"}]}

        async def embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    provider = Provider()
    repo = PostgresMemoryRepository(sessions)
    service = HindsightService(repo, provider)
    value = RetainInput(
        document_id=str(doc_id), title="cached", content="Source text", file_type="text"
    )
    assert (await service.retain(value)).status == "success"
    assert (await service.retain(value)).status == "success"
    assert provider.calls == 1
    assert (await service.retain(replace(value, policy_version=2))).status == "success"
    assert provider.calls == 2
    hidden = repo.with_scope(MemoryScope(bank_id="other"))
    with pytest.raises(ValueError, match="document does not exist"):
        await hidden.retention_extraction_cache(str(doc_id))
    provider.fail = True
    changed = replace(value, content="Different source")
    assert (await service.retain(changed)).status == "degraded"
    assert await repo.retention_extraction_cache(str(doc_id)) == {}
    provider.fail = False
    assert (await service.retain(changed)).status == "success"
    assert provider.calls == 4
    assert (
        await service.retain(replace(changed, force_extraction=True))
    ).status == "success"
    assert provider.calls == 5


async def test_retention_request_replay_conflict_and_concurrent_commit(scope_database):
    import asyncio
    from dataclasses import replace
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.components.store.models import EMBEDDING_DIM
    from src.engine.hindsight_components.models import RetentionRequest
    from src.engine.hindsight_components.service import HindsightService
    from src.engine.hindsight_components.types import (
        RetainInput,
        RetentionRequestConflict,
    )

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessions() as session:
        session.add(Document(id=doc_id, title="idempotent", file_type="text"))
        await session.commit()

    class Provider:
        calls = 0
        ready = asyncio.Event()

        async def json(self, system, user, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self.ready.set()
            await asyncio.wait_for(self.ready.wait(), timeout=5)
            return {"facts": [{"text": "A fact", "type": "world"}]}

        async def embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    provider = Provider()
    repo = PostgresMemoryRepository(sessions)
    service = HindsightService(repo, provider)
    value = RetainInput(
        document_id=str(doc_id),
        title="idempotent",
        file_type="text",
        content="A fact",
        request_id="request-1",
        expected_revision=0,
    )
    first, second = await asyncio.gather(service.retain(value), service.retain(value))
    assert first == second
    assert first.revision == 1
    assert await repo.retention_revision(str(doc_id)) == 1
    assert await service.retain(value) == first
    assert provider.calls == 2
    with pytest.raises(RetentionRequestConflict):
        await service.retain(replace(value, content="Changed content"))
    assert provider.calls == 2
    async with sessions() as session:
        assert len(list(await session.scalars(select(RetentionRequest)))) == 1
    hidden = service.with_scope(MemoryScope(bank_id="other"))
    with pytest.raises(ValueError, match="document does not exist"):
        await hidden.retain(value)

    def fail_before_commit(*args, **kwargs):
        raise RuntimeError("injected publication failure")

    repo._enqueue_graph_event = fail_before_commit
    with pytest.raises(RuntimeError, match="injected publication failure"):
        await service.retain(
            replace(
                value,
                request_id="request-rollback",
                expected_revision=1,
                content="Uncommitted replacement",
            )
        )
    assert await service.retention_revision(str(doc_id)) == 1
    async with sessions() as session:
        assert await session.get(RetentionRequest, (doc_id, "request-rollback")) is None
        assert set(
            await session.scalars(
                select(MemoryUnit.source_text).where(MemoryUnit.document_id == doc_id)
            )
        ) == {"A fact"}


async def test_retention_revision_fences_concurrent_publication_and_failure(
    scope_database,
):
    import asyncio
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.models import HindsightDocumentState
    from src.engine.hindsight_components.types import RetentionRevisionConflict

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessions() as session:
        session.add(Document(id=doc_id, title="revision", file_type="text"))
        await session.commit()
    repo = PostgresMemoryRepository(sessions)
    assert await repo.retention_revision(str(doc_id)) == 0
    plans = [
        RetainPlan(
            document_id=str(doc_id),
            title="revision",
            file_type="text",
            source_type="test",
            memories=[],
            links=[],
            expected_revision=0,
        )
        for _ in range(2)
    ]
    results = await asyncio.gather(
        *(repo.replace_document(plan) for plan in plans), return_exceptions=True
    )
    assert sum(isinstance(result, RetentionRevisionConflict) for result in results) == 1
    assert sum(result is None for result in results) == 1
    assert await repo.retention_revision(str(doc_id)) == 1
    with pytest.raises(RetentionRevisionConflict):
        await repo.set_document_state(str(doc_id), "failed", expected_revision=0)
    async with sessions() as session:
        state = await session.get(HindsightDocumentState, doc_id)
        assert state.status == "indexed"
        assert state.revision == 1
    with pytest.raises(ValueError, match="document does not exist"):
        await repo.with_scope(MemoryScope(bank_id="other")).retention_revision(
            str(doc_id)
        )


async def test_entity_upgrade_preserves_legacy_identity_and_mentions(scope_database):
    from src.engine.components.store.entity_migration import migrate_entities
    from src.engine.hindsight_components.models import MemoryUnitEntity

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    ids = [uuid.uuid4() for _ in range(3)]
    async with engine.begin() as conn:
        await conn.execute(
            insert(Document).values(id=ids[0], title="legacy", file_type="text")
        )
        await conn.execute(
            insert(MemoryUnit).values(
                id=ids[1],
                document_id=ids[0],
                chunk_index=0,
                memory_index=0,
                text="Alice",
                source_text="Alice",
            )
        )
        await conn.execute(
            insert(MemoryEntity).values(
                id=ids[2], canonical_name="Alice", normalized_name="alice"
            )
        )
        await conn.execute(
            insert(MemoryUnitEntity).values(memory_id=ids[1], entity_id=ids[2])
        )
        await conn.execute(
            text("ALTER TABLE memory_entities DROP COLUMN identity_key CASCADE")
        )
        await conn.execute(
            text(
                "ALTER TABLE memory_entities ADD CONSTRAINT uq_memory_entities_bank_name UNIQUE(bank_id, normalized_name)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE memory_unit_entities DROP COLUMN original_name, DROP COLUMN aliases"
            )
        )
    await migrate_entities(engine, schema=schema)
    await migrate_entities(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        entity = await session.get(MemoryEntity, ids[2])
        mention = await session.get(MemoryUnitEntity, (ids[1], ids[2]))
        assert entity.identity_key == ""
        assert mention.original_name == "Alice"
        assert mention.aliases == []
        session.add(
            MemoryEntity(
                canonical_name="Alice",
                normalized_name="alice",
                identity_key="different-person",
            )
        )
        await session.commit()
    await migrate_scope(engine, schema=schema)


async def test_contextual_entities_aliases_and_equal_names_persist_separately(
    scope_database,
    scope_graph,
):
    import json
    from src.engine.components.store.entity_migration import migrate_entities
    from src.engine.components.store.models import EMBEDDING_DIM, MemoryBank
    from src.engine.hindsight_components.config import HindsightOptions
    from src.engine.hindsight_components.models import MemoryUnitEntity
    from src.engine.hindsight_components.service import HindsightService

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_entities(engine, schema=schema)
    await migrate_entities(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    driver, prefix = scope_graph
    bank = prefix + "entities"
    async with sessions() as session:
        session.add(MemoryBank(id=bank, name=bank))
        await session.commit()

    class Provider:
        async def embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

        async def json(self, system, user, **kwargs):
            if system.startswith("Resolve entity"):
                data = json.loads(user)
                if "doctor" in data["context"]:
                    return {"decision": "distinct", "entity_id": None}
                return {"decision": "match", "entity_id": data["candidates"][0]["id"]}
            content = user.split("TEXT:\n", 1)[1].split("\n\nReturn", 1)[0]
            name = "AC" if content.startswith("AC ") else "Alice"
            return {
                "facts": [
                    {
                        "text": content,
                        "type": "world",
                        "entities": [name],
                        "entity_aliases": {
                            name: ["AC"] if name == "Alice" else ["Alice"]
                        },
                    }
                ]
            }

    repo = PostgresMemoryRepository(sessions, scope=MemoryScope(bank_id=bank))
    service = HindsightService(
        repo, Provider(), HindsightOptions(entity_resolution_enabled=True)
    )
    documents = []
    for content in (
        "Alice is an Acme engineer",
        "AC shipped the Acme project",
        "Alice is a different hospital doctor",
    ):
        doc_id = uuid.uuid4()
        documents.append(doc_id)
        async with sessions() as session:
            session.add(
                Document(
                    id=doc_id,
                    bank_id=bank,
                    title="source",
                    file_type="text",
                    raw_text=content,
                )
            )
            await session.commit()
        result = await service.retain(
            document_id=str(doc_id), title="source", content=content, file_type="text"
        )
        assert result.status == "success"
        assert result.stage_results["entities"] == "success"
    async with sessions() as session:
        rows = (
            await session.execute(
                select(
                    MemoryUnit.document_id,
                    MemoryEntity.id,
                    MemoryUnit.metadata_json["entity_mentions"],
                )
                .join(MemoryUnitEntity, MemoryUnitEntity.memory_id == MemoryUnit.id)
                .join(MemoryEntity, MemoryEntity.id == MemoryUnitEntity.entity_id)
            )
        ).all()
        mapping = {doc: (entity, names) for doc, entity, names in rows}
        assert mapping[documents[0]][0] == mapping[documents[1]][0]
        assert mapping[documents[2]][0] != mapping[documents[0]][0]
        assert mapping[documents[1]][1] == ["AC"]
        entities = list(await session.scalars(select(MemoryEntity)))
        assert len(entities) == 2
        assert {e.canonical_name for e in entities} == {"Alice"}
    # Scope migration must not restore name-only uniqueness after disambiguation.
    await migrate_scope(engine, schema=schema)
    await migrate_entities(engine, schema=schema)
    from src.engine.hindsight_components.neo4j_graph import HindsightNeo4jGraphStore

    store = HindsightNeo4jGraphStore(driver).with_scope(repo.scope)
    await store.ensure_schema()
    for document in documents:
        await store.replace_document(await repo.graph_projection(str(document)))
    from src.engine.hindsight_components.models import (
        MemoryEntityCorrection,
        MemoryLink,
        HindsightGraphOutbox,
    )

    async with sessions() as session:
        facts = list(
            await session.scalars(
                select(MemoryUnit).where(MemoryUnit.is_source_chunk.is_(False))
            )
        )
        fact_ids = {f.document_id: f.id for f in facts}
    moved = await service.correct_entity(
        str(mapping[documents[0]][0]),
        [str(fact_ids[documents[1]])],
        target_entity_id=str(mapping[documents[2]][0]),
        reason="Correct a mistaken alias",
    )
    assert moved["target_entity_id"] == str(mapping[documents[2]][0])
    async with sessions() as session:
        mention = await session.get(
            MemoryUnitEntity, (fact_ids[documents[1]], mapping[documents[2]][0])
        )
        assert mention.original_name == "AC"
        assert list(await session.scalars(select(MemoryEntityCorrection)))
        links = list(
            await session.scalars(
                select(MemoryLink).where(MemoryLink.link_type == "entity")
            )
        )
        assert any(
            {link.source_memory_id, link.target_memory_id}
            == {fact_ids[documents[1]], fact_ids[documents[2]]}
            for link in links
        )
        assert await session.scalar(
            select(HindsightGraphOutbox.id)
            .where(HindsightGraphOutbox.document_id == documents[1])
            .limit(1)
        )
    separated = await service.correct_entity(
        moved["target_entity_id"],
        [str(fact_ids[documents[1]])],
        reason="Split the ambiguous person",
    )
    assert separated["target_entity_id"] != moved["target_entity_id"]
    projection = await repo.graph_projection(str(documents[1]))
    assert {entity.id for entity in projection.entities} == {
        separated["target_entity_id"]
    }
    async with sessions() as session:
        assert not list(
            await session.scalars(
                select(MemoryLink).where(MemoryLink.link_type == "entity")
            )
        )
        assert len(list(await session.scalars(select(MemoryEntityCorrection)))) == 2
    hidden = service.with_scope(MemoryScope(bank_id="unavailable"))
    with pytest.raises(ValueError, match="memory does not exist"):
        await hidden.correct_entity(
            separated["target_entity_id"],
            [str(fact_ids[documents[1]])],
            reason="unauthorized",
        )
    for document in documents:
        await store.replace_document(await repo.graph_projection(str(document)))
    async with driver.session() as session:
        result = await session.run(
            "MATCH (e:HindsightEntity {bank_id:$bank, normalized_name:'alice'}) RETURN count(e) AS count",
            bank=bank,
        )
        assert (await result.single())["count"] == 3
        result = await session.run(
            "MATCH (m:HindsightMemory {document_id:$document})-[:MENTIONS]->(e:HindsightEntity) RETURN e.id AS id",
            document=str(documents[1]),
        )
        assert {r["id"] async for r in result} == {separated["target_entity_id"]}


async def test_file_extraction_replay_preserves_time_and_checks_scope(scope_database):
    from datetime import UTC, datetime
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.service import HindsightService
    from src.engine.hindsight_components.tests.fakes import FakeProviders
    from src.engine.hindsight_components.types import RetainInput

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessions() as session:
        session.add(
            Document(
                id=doc_id,
                title="old report",
                raw_text="Alice worked yesterday",
                file_type="markdown",
                tags=["private"],
            )
        )
        await session.commit()
    repo = PostgresMemoryRepository(
        sessions, scope=MemoryScope(visibility=TagFilter(("private",), "all_strict"))
    )

    class DatabaseProviders(FakeProviders):
        async def embed(self, texts, **kwargs):
            from src.engine.components.store.models import EMBEDDING_DIM

            return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    service = HindsightService(repo, DatabaseProviders())
    result = await service.retain(
        RetainInput(
            document_id=str(doc_id),
            title="old report",
            content="Alice worked yesterday",
            file_type="markdown",
            agent_name="helper",
            speakers={"user": "alice"},
            source_timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            reference_timezone="Asia/Shanghai",
            metadata={"entity_aliases": {"alice": ["ac"]}},
        )
    )
    assert result.status == "success"
    aliases = await repo.entity_candidates(("ac",), limit=10)
    assert len(aliases) == 1
    assert aliases[0].name == "Alice"
    assert aliases[0].aliases == ("ac",)
    assert aliases[0].evidence
    hidden_repo = repo.with_scope(
        MemoryScope(visibility=TagFilter(("other",), "all_strict"))
    )
    assert await hidden_repo.entity_candidates(("alice", "ac"), limit=10) == []
    replay = await repo.retention_input(str(doc_id))
    assert replay.source_timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert replay.reference_timezone == "Asia/Shanghai"
    assert replay.speakers["user"] == "alice"
    assert (await service.reprocess_document(str(doc_id))).status == "success"
    with pytest.raises(ValueError, match="document does not exist"):
        await repo.with_scope(
            MemoryScope(visibility=TagFilter(("other",), "all_strict"))
        ).retention_input(str(doc_id))
    with pytest.raises(ValueError, match="unsupported retention stage"):
        await service.reprocess_document(str(doc_id), stage="unknown")


async def test_retention_lease_fences_state_and_memory_commit(scope_database):
    from datetime import datetime, timedelta, timezone
    from src.engine.hindsight_components.conversation_queue import (
        PostgresConversationMemoryQueue,
    )
    from src.engine.hindsight_components.models import ConversationMemorySource
    from src.engine.hindsight_components.types import RetentionLeaseLost
    from src.engine.components.store.retention_migration import migrate_retention

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = PostgresConversationMemoryQueue(sessions)
    pending = await queue.enqueue(
        session_id="lease-session", turn_id="turn", content="original"
    )
    now = datetime.now(timezone.utc)
    first = (await queue.claim(now=now, lease_seconds=60))[0]
    # A worker with a shorter local setting cannot steal the stored lease early.
    assert await queue.claim(now=now + timedelta(seconds=30), lease_seconds=1) == []
    second = (await queue.claim(now=now + timedelta(seconds=61), lease_seconds=60))[0]
    assert first.operation_id == second.operation_id == pending.operation_id
    assert first.lease_token != second.lease_token
    assert (
        await queue.complete(first.document_id, lease_token=first.lease_token) is False
    )
    assert (
        await queue.fail(
            first.document_id, "stale failure", lease_token=first.lease_token
        )
        == "cancelled"
    )
    assert await queue.get_status(first.document_id) == "processing"
    repo = PostgresMemoryRepository(sessions)
    plan = RetainPlan(
        document_id=first.document_id,
        title="test",
        file_type="conversation",
        source_type="conversation",
        memories=[],
        links=[],
    )
    with pytest.raises(RetentionLeaseLost):
        await repo.with_lease(first.document_id, first.lease_token).replace_document(
            plan
        )
    await repo.with_lease(second.document_id, second.lease_token).replace_document(plan)
    assert (
        await queue.complete(second.document_id, lease_token=second.lease_token) is True
    )
    with pytest.raises(RetentionLeaseLost):
        await repo.with_lease(second.document_id, second.lease_token).replace_document(
            plan
        )
    async with sessions() as session:
        row = await session.get(ConversationMemorySource, uuid.UUID(first.document_id))
        assert row.stage_results == {"delivery": "accepted", "retain": "completed"}
        assert row.error_msg is None
    # Exercise upgrade from the previous schema, then repeat it without changes.
    async with engine.begin() as conn:
        for column in (
            "operation_id",
            "stage_results",
            "lease_token",
            "lease_expires_at",
        ):
            await conn.execute(
                text(f"ALTER TABLE conversation_memory_sources DROP COLUMN {column}")
            )
    await migrate_retention(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    async with sessions() as session:
        row = await session.get(ConversationMemorySource, uuid.UUID(first.document_id))
        assert str(row.operation_id) == first.document_id
        assert row.stage_results["retain"] == "completed"
    doomed = await queue.enqueue(
        session_id="crash", turn_id="last", content="last attempt"
    )
    claimed = (await queue.claim(max_attempts=1, lease_seconds=1))[0]
    assert claimed.document_id == doomed.document_id
    assert (
        await queue.claim(
            max_attempts=1, now=datetime.now(timezone.utc) + timedelta(seconds=2)
        )
        == []
    )
    assert await queue.get_status(doomed.document_id) == "failed"
    assert await queue.retry_stage(doomed.document_id)
    retry = (await queue.claim())[0]
    assert retry.attempts == 1
    assert retry.operation_id == doomed.operation_id
    assert not await queue.retry_stage(retry.document_id)
    assert not await queue.record_stages(
        retry.document_id, {"extract": "success"}, lease_token=str(uuid.uuid4())
    )
    assert await queue.record_stages(
        retry.document_id, {"extract": "degraded"}, lease_token=retry.lease_token
    )
    await queue.cancel_session("crash")
    assert not await queue.retry_stage(retry.document_id)
    assert not await queue.complete(retry.document_id, lease_token=retry.lease_token)


async def test_conversation_queue_and_worker_preserve_bank_and_tags(scope_database):
    from src.engine.components.store.models import MemoryBank
    from src.engine.hindsight_components.conversation_queue import (
        PostgresConversationMemoryQueue,
        conversation_document_id,
    )
    from src.engine.hindsight_components.conversation_worker import (
        ConversationRetentionWorker,
    )

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([MemoryBank(id=b, name=b) for b in ("A", "B")])
        await session.commit()
    scopes = [
        MemoryScope(bank_id=b, visibility=TagFilter(("u:1",), "all_strict"))
        for b in ("A", "B")
    ]
    queues = [
        PostgresConversationMemoryQueue(sessions, scope=s, write_tags=("u:1",))
        for s in scopes
    ]
    jobs = [
        await q.enqueue(
            session_id="same-session", turn_id="same-turn", content="private"
        )
        for q in queues
    ]
    assert jobs[0].document_id != jobs[1].document_id
    assert str(conversation_document_id("same-session", "same-turn")) not in {
        j.document_id for j in jobs
    }
    assert await queues[0].get_status(jobs[1].document_id) is None
    assert await queues[0].complete(jobs[1].document_id) is False
    assert await queues[0].delete_documents([jobs[1].document_id]) == 0
    assert await queues[0].session_document_ids("same-session") == [jobs[0].document_id]
    assert (await PostgresConversationMemoryQueue(sessions).status_counts()).total == 0
    assert (await queues[0].status_counts()).pending == 1
    import asyncio

    repeated = await asyncio.gather(
        *[
            queues[0].enqueue(
                session_id="same-session", turn_id="same-turn", content="private"
            )
            for _ in range(3)
        ]
    )
    assert {job.document_id for job in repeated} == {jobs[0].document_id}
    with pytest.raises(ValueError, match="conversation_delivery_conflict"):
        await queues[0].enqueue(
            session_id="same-session", turn_id="same-turn", content="changed"
        )
    with pytest.raises(ValueError, match="conversation_delivery_conflict"):
        await queues[0].enqueue(
            session_id="same-session",
            turn_id="same-turn",
            content="private",
            request_fingerprint="0" * 64,
        )
    async with sessions() as session:
        original = await session.get(Document, uuid.UUID(jobs[0].document_id))
        assert original.raw_text == "private"
    seen = []

    class Retainer:
        def __init__(self, scope=None, lease=None):
            self.scope = scope or MemoryScope()
            self.lease = lease

        def with_scope(self, scope):
            return Retainer(scope)

        def with_lease(self, document_id, lease_token):
            return Retainer(self.scope, (document_id, lease_token))

        async def retain(self, value):
            seen.append((self.scope.bank_id, value.tags))
            repo = PostgresMemoryRepository(session_factory=sessions, scope=self.scope)
            if self.lease:
                repo = repo.with_lease(*self.lease)
            await repo.replace_document(
                RetainPlan(
                    document_id=value.document_id,
                    title=value.title,
                    file_type=value.file_type,
                    source_type=value.source_type,
                    memories=[
                        MemoryDraft(
                            id=str(uuid.uuid4()),
                            document_id=value.document_id,
                            memory_type="world",
                            context="",
                            chunk_index=0,
                            memory_index=0,
                            text=value.content,
                            source_text=value.content,
                            tags=value.tags,
                            entities=["Alice"],
                            embedding=[0.1] * 768,
                        )
                    ],
                    links=[],
                )
            )

    worker = ConversationRetentionWorker(
        PostgresConversationMemoryQueue(sessions, all_banks=True),
        Retainer(),
        PostgresMemoryRepository(session_factory=sessions),
        max_concurrent=2,
    )
    result = await worker.run_once()
    assert result.completed == 2
    assert {
        b for b, tags in seen if "u:1" in tags and "session:same-session" in tags
    } == {"A", "B"}
    assert (await queues[1].status_counts()).completed == 1
    assert await queues[0].delete_documents([jobs[0].document_id]) == 1
    assert await queues[1].get_status(jobs[1].document_id) == "completed"


@pytest.fixture
async def scope_graph():
    from neo4j import AsyncGraphDatabase

    uri = os.getenv("SCOPE_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("SCOPE_TEST_NEO4J_URI must select a disposable graph")
    prefix = "scope_graph_" + uuid.uuid4().hex
    driver = AsyncGraphDatabase.driver(uri, auth=("neo4j", "tkb-scope-test-only"))
    try:
        await driver.verify_connectivity()
        yield driver, prefix
    finally:
        async with driver.session() as session:
            await session.run(
                "MATCH (n) WHERE n.bank_id STARTS WITH $prefix DETACH DELETE n",
                prefix=prefix,
            )
        await driver.close()


async def test_real_graph_projection_restores_event_bank(scope_database, scope_graph):
    from src.engine.components.store.models import MemoryBank
    from src.engine.hindsight_components.graph_outbox import (
        PostgresGraphOutbox,
        GraphProjectionWorker,
    )
    from src.engine.hindsight_components.graph_projector import MemoryGraphProjector
    from src.engine.hindsight_components.neo4j_graph import HindsightNeo4jGraphStore

    engine, schema = scope_database
    driver, prefix = scope_graph
    await migrate_scope(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    banks = [prefix + suffix for suffix in ("A", "B")]
    ids = [uuid.uuid4(), uuid.uuid4()]
    async with sessions() as session:
        session.add_all([MemoryBank(id=b, name=b) for b in banks])
        await session.flush()
        session.add_all(
            [
                Document(id=d, bank_id=b, title="fixture", file_type="markdown")
                for d, b in zip(ids, banks)
            ]
        )
        await session.commit()
    for doc_id, bank in zip(ids, banks):
        repo = PostgresMemoryRepository(
            session_factory=sessions, scope=MemoryScope(bank_id=bank)
        )
        await repo.replace_document(
            RetainPlan(
                document_id=str(doc_id),
                title="fixture",
                file_type="markdown",
                source_type="test",
                memories=[
                    MemoryDraft(
                        id=str(uuid.uuid4()),
                        document_id=str(doc_id),
                        memory_type="world",
                        context="",
                        chunk_index=0,
                        memory_index=0,
                        text=bank,
                        source_text=bank,
                        entities=["Alice"],
                        embedding=[0.1] * 768,
                    )
                ],
                links=[],
            )
        )
    store = HindsightNeo4jGraphStore(driver)
    await store.ensure_schema()
    worker = GraphProjectionWorker(
        PostgresGraphOutbox(sessions),
        PostgresMemoryRepository(session_factory=sessions),
        MemoryGraphProjector(store),
    )
    results = await worker.drain()
    assert len(results) == 2 and all(r.status == "completed" for r in results)
    async with driver.session() as session:
        result = await session.run(
            "MATCH (e:HindsightEntity) WHERE e.bank_id IN $banks RETURN e.bank_id AS bank, e.normalized_name AS name",
            banks=banks,
        )
        assert {r["bank"] for r in await result.data()} == set(banks)
        result = await session.run(
            "MATCH (a)-[r]->(b) WHERE a.bank_id IN $banks AND a.bank_id <> b.bank_id RETURN count(r) AS count",
            banks=banks,
        )
        assert (await result.single())["count"] == 0
    await store.with_scope(MemoryScope(bank_id=banks[0])).delete_document(str(ids[1]))
    async with driver.session() as session:
        result = await session.run(
            "MATCH (m:HindsightMemory {document_id:$id}) RETURN count(m) AS count",
            id=str(ids[1]),
        )
        assert (await result.single())["count"] == 1


async def test_graphrag_sources_and_vector_topk_are_isolated(
    scope_database, scope_graph, monkeypatch
):
    from src.engine.components.store.models import Chunk, MemoryBank
    from src.engine.components.store.source_graph import SourceNeo4jClient
    from src.engine.components.store.neo4j import EntityData, EntitySource, RelationData
    from src.engine.graphrag import _search

    engine, schema = scope_database
    driver, prefix = scope_graph
    await migrate_scope(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    banks = [prefix + "A", prefix + "B"]
    ids = [uuid.uuid4() for _ in range(3)]
    async with sessions() as session:
        session.add_all([MemoryBank(id=b, name=b) for b in banks])
        await session.flush()
        for index, (doc_id, bank, tags) in enumerate(
            zip(ids, [banks[0], banks[1], banks[0]], [["u:1"], ["u:1"], ["u:2"]])
        ):
            session.add(
                Document(
                    id=doc_id,
                    bank_id=bank,
                    tags=tags,
                    title="same.md",
                    file_type="markdown",
                )
            )
            await session.flush()
            session.add(
                Chunk(
                    doc_id=doc_id,
                    bank_id=bank,
                    tags=tags,
                    chunk_index=0,
                    chunk_text=f"text-{index}",
                    doc_uri=f"{doc_id}:same.md",
                    embedding=[0.1] * 768,
                )
            )
        await session.commit()
    graph = SourceNeo4jClient(driver=driver, session_factory=sessions)
    await graph.ensure_schema()
    for i, doc_id in enumerate(ids):
        source = EntitySource(str(doc_id), 0, "same.md")
        await graph.upsert_document_node(str(doc_id), "same.md", "markdown")
        await graph.upsert_entities_batch(
            [
                (EntityData("Alice", "Person", f"description-{i}"), source),
                (EntityData(f"Project-{i}", "Project"), source),
            ]
        )
        await graph.upsert_relation(
            RelationData("Alice", f"Project-{i}", "WORKS_ON", f"relation-{i}"), source
        )
    # A malicious/stale graph edge must not expose another bank via related docs.
    async with driver.session() as session:
        await session.run(
            "MATCH (a:Document {doc_id:$a}), (b:Document {doc_id:$b}) CREATE (a)-[:RELATED_TO {reason:'hidden'}]->(b)",
            a=str(ids[0]),
            b=str(ids[1]),
        )
    scope = MemoryScope(bank_id=banks[0], visibility=TagFilter(("u:1",), "all_strict"))
    import json

    async with driver.session() as session:
        await session.run(
            "CREATE (e:LegacyEntity {name:'MixedLegacy', bank_id:$bank, sources:$sources, description:'includes hidden source'})",
            bank=banks[0],
            sources=json.dumps(
                [{"doc_id": str(ids[i]), "chunk_index": 0} for i in (0, 2)]
            ),
        )
    await graph.ensure_schema()
    await graph.ensure_schema()
    bound = graph.with_scope(scope)
    result = await bound.get_full_graph()
    assert {n["name"] for n in result["nodes"]} == {"Alice", "Project-0"}
    assert (await bound.get_entity_details("Alice")).properties[
        "description"
    ] == "description-0"
    assert {n.name for n in await bound.query_neighbors("Alice")} == {"Project-0"}
    assert await bound.get_document_entities(str(ids[1])) == []
    assert await bound.get_related_docs([str(ids[0])]) == []
    assert (
        await graph.with_scope(MemoryScope(bank_id=banks[0])).get_entity_details(
            "MixedLegacy"
        )
    ) is not None

    async def embed(_query):
        return [0.1] * 768

    monkeypatch.setattr(_search.embedder, "embed_text", embed)
    from types import SimpleNamespace

    monkeypatch.setattr(
        _search,
        "get_reranker",
        lambda: SimpleNamespace(rerank=lambda _q, texts: [1.0] * len(texts)),
    )
    async with sessions() as session:
        candidates = await _search.vector_search(session, "same", top_k=1, scope=scope)
        assert [c["doc_id"] for c in candidates] == [str(ids[0])]
        recalled = await _search.full_search(
            session, graph, "same", top_k=1, scope=scope
        )
        assert [c.doc_id for c in recalled.chunks] == [str(ids[0])]
        assert recalled.related_docs == []
        assert "relation-1" not in str(recalled.related_entities)
        assert "relation-2" not in str(recalled.related_entities)
    async with sessions() as session:
        await session.delete(await session.get(Document, ids[0]))
        await session.commit()
    assert await bound.get_full_graph() == {"nodes": [], "links": []}


async def test_policy_publish_is_scoped_and_fences_concurrent_edits(scope_database):
    import asyncio
    from src.engine.components.store.models import MemoryBank
    from src.engine.components.store.scope_policy import (
        ScopePolicyStore,
        PolicyVersionConflict,
    )
    from src.engine.scope_policy import ScopePolicy

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([MemoryBank(id=b, name=b) for b in ("A", "B")])
        await session.commit()
    a = ScopePolicyStore(sessions, scope=MemoryScope(bank_id="A"))
    b = ScopePolicyStore(sessions, scope=MemoryScope(bank_id="B"))
    assert (await a.read()).version == 1
    results = await asyncio.gather(
        a.publish(ScopePolicy(agent_name="Alice"), expected_version=1),
        a.publish(ScopePolicy(agent_name="Bob"), expected_version=1),
        return_exceptions=True,
    )
    assert sum(isinstance(r, PolicyVersionConflict) for r in results) == 1
    assert (await a.read()).version == 2
    assert (await a.read()).policy.agent_name in {"Alice", "Bob"}
    assert (await b.read()).version == 1
    assert (await b.read()).policy.agent_name is None


async def test_document_access_and_background_chunk_ownership(
    scope_database, monkeypatch
):
    from types import SimpleNamespace
    from src.engine.graphrag import backend as backend_module
    from src.engine.graphrag.backend import GraphRAGBackend
    from src.engine.graphrag.pipeline import Pipeline
    from src.engine.components.store.models import Chunk, MemoryBank

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(backend_module, "async_session_factory", sessions)
    ids = [uuid.uuid4() for _ in range(3)]
    async with sessions() as session:
        session.add_all([MemoryBank(id=b, name=b) for b in ("A", "B")])
        await session.flush()
        for doc_id, bank, tags in zip(
            ids, ("A", "B", "A"), (["user:1"], ["user:1"], ["user:2"])
        ):
            session.add(
                Document(
                    id=doc_id,
                    bank_id=bank,
                    tags=tags,
                    title="same.md",
                    file_type="markdown",
                    raw_text="same content",
                    status="indexed",
                )
            )
        await session.commit()

    backend = GraphRAGBackend(
        SimpleNamespace(),
        SimpleNamespace(),
        scope=MemoryScope(bank_id="A", visibility=TagFilter(("user:1",), "all_strict")),
    )
    listing = await backend.list_documents()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == str(ids[0])
    assert (await backend.get_document(str(ids[0])))["raw_text"] == "same content"
    for hidden in ids[1:]:
        assert await backend.get_document(str(hidden)) is None
        with pytest.raises(ValueError, match="文档不存在"):
            await backend.edit_content(str(hidden), "overwrite")
        with pytest.raises(ValueError, match="文档不存在"):
            await backend.reingest(str(hidden))
        await backend.remove(str(hidden))
    default = GraphRAGBackend(SimpleNamespace(), SimpleNamespace())
    assert (await default.list_documents())["total"] == 0

    # Exercise real HTTP dependency resolution against the same PostgreSQL data.
    import asyncio
    from hashlib import sha256
    from httpx import AsyncClient, ASGITransport
    from config.schema import AppConfig
    from config.settings import settings
    from src.engine.trusted_scope import ScopeBinding
    from src.frontend.webapp.server import app as app_module, deps
    from config import schema as config_schema

    config = AppConfig.model_validate(
        {"engine": {"memory": {"enabled": True, "features": {"scope": True}}}}
    )
    monkeypatch.setattr(deps, "_app_config", config)
    monkeypatch.setattr(config_schema, "load_config", lambda *_: config)
    monkeypatch.setattr(
        settings,
        "memory_scope_bindings",
        {
            sha256(b"a").hexdigest(): ScopeBinding(
                bank_id="A",
                visibility={"tags": ["user:1"], "match": "all_strict"},
                write_tags=("user:1",),
            ),
            sha256(b"b").hexdigest(): ScopeBinding(bank_id="B"),
        },
    )
    monkeypatch.setattr(deps, "_kb", default)
    monkeypatch.setattr(
        deps.settings, "memory_scope_bindings", settings.memory_scope_bindings
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        a, b, public = await asyncio.gather(
            client.get("/api/documents", headers={"x-tkb-scope-token": "a"}),
            client.get("/api/documents", headers={"x-tkb-scope-token": "b"}),
            client.get("/api/documents?bank_id=B"),
        )
        assert [d["id"] for d in a.json()["items"]] == [str(ids[0])]
        assert [d["id"] for d in b.json()["items"]] == [str(ids[1])]
        assert public.json()["total"] == 0
        assert (
            await client.get("/api/documents", headers={"x-tkb-scope-token": "forged"})
        ).status_code == 403
        assert (
            await client.get(
                f"/api/documents/{ids[1]}", headers={"x-tkb-scope-token": "a"}
            )
        ).status_code == 404
        assert (
            await client.put(
                f"/api/documents/{ids[1]}/content",
                headers={"x-tkb-scope-token": "a"},
                json={"content": "overwrite", "bank_id": "B"},
            )
        ).status_code == 404

    # Stateful MCP: every tool request carries its own trusted HTTP credential.
    from mcp.server.fastmcp import FastMCP
    from src.agent.tkb.mcp import server as mcp_module

    mcp = FastMCP("scope-test", streamable_http_path="/", json_response=True)
    monkeypatch.setattr(mcp_module, "mcp", mcp)
    monkeypatch.setattr(mcp_module, "_kb", default)
    mcp.tool()(mcp_module.list_documents)
    mcp_app = mcp.streamable_http_app()
    async with mcp.session_manager.run():
        async with AsyncClient(
            transport=ASGITransport(app=mcp_app), base_url="http://localhost:8123"
        ) as client:
            headers = {"accept": "application/json, text/event-stream"}
            init = await client.post(
                "/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "scope-test", "version": "1"},
                    },
                },
            )
            assert init.status_code == 200, init.text
            headers["mcp-session-id"] = init.headers["mcp-session-id"]
            headers["mcp-protocol-version"] = "2025-03-26"
            await client.post(
                "/",
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

            async def call(request_id, token):
                result = await client.post(
                    "/",
                    headers={
                        **headers,
                        **({"x-tkb-scope-token": token} if token else {}),
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": "tools/call",
                        "params": {"name": "list_documents", "arguments": {}},
                    },
                )
                return result.json()["result"]

            first = await call(2, "a")
            second = await call(3, "b")
            missing = await call(4, None)
            forged = await call(5, "forged")
            assert first["structuredContent"]["items"][0]["id"] == str(ids[0])
            assert second["structuredContent"]["items"][0]["id"] == str(ids[1])
            assert missing["structuredContent"]["total"] == 0
            assert forged["isError"] is True

    # Compatible rollback disables isolated entry without exposing its data to
    # old unscoped requests, and does not remove scoped rows or pending work.
    monkeypatch.setattr(deps, "_app_config", AppConfig())
    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://localhost"
    ) as client:
        assert (
            await client.get("/api/documents", headers={"x-tkb-scope-token": "a"})
        ).status_code == 403
        assert (await client.get("/api/documents")).json()["total"] == 0

    # The background pipeline has no request scope: ownership must come from DB.
    pipeline = Pipeline(SimpleNamespace(), analyzer=SimpleNamespace())
    async with sessions() as session:
        await pipeline._persist_chunks(
            session,
            doc_id=ids[1],
            title="same.md",
            raw_text="same content",
            content_hash="same-hash",
            overview="",
            chunks=[SimpleNamespace(index=0, text="same content", token_count=2)],
            embeddings=[[0.1] * 768],
        )
    async with sessions() as session:
        chunk = (await session.execute(select(Chunk))).scalar_one()
        assert chunk.bank_id == "B"
        assert chunk.tags == ["user:1"]
        assert (await session.get(Document, ids[2])).raw_text == "same content"
    from src.engine.graphrag import pipeline as pipeline_module
    from src.engine.hindsight_components.hook import HindsightRetainHook
    from src.engine.hindsight_components.service import HindsightService

    class Providers:
        async def embed(self, texts, **_kwargs):
            return [[0.1] * 768 for _ in texts]

        async def json(self, *_args, **_kwargs):
            return {
                "facts": [
                    {"text": "Alice works here", "type": "world", "entities": ["Alice"]}
                ]
            }

    repo = PostgresMemoryRepository(session_factory=sessions)
    hook = HindsightRetainHook(HindsightService(repo, Providers()), repo)
    pipeline = Pipeline(SimpleNamespace(), analyzer=SimpleNamespace(), index_hook=hook)
    monkeypatch.setattr(pipeline_module, "async_session_factory", sessions)
    await pipeline._notify_indexed(
        document_id=str(ids[1]),
        title="same.md",
        content="Alice works here",
        file_type="markdown",
    )
    async with sessions() as session:
        memories = (
            await session.scalars(
                select(MemoryUnit).where(MemoryUnit.document_id == ids[1])
            )
        ).all()
        assert memories
        assert all(
            m.bank_id == "B" and m.scope_tags == ["user:1"] and "user:1" in m.tags
            for m in memories
        )
    exact = PostgresMemoryRepository(
        session_factory=sessions,
        scope=MemoryScope(bank_id="B", visibility=TagFilter(("user:1",), "exact")),
    )
    assert await exact.keyword_search("Alice", 10)


@pytest.fixture
async def scope_database():
    dsn = os.getenv("SCOPE_TEST_DSN")
    if not dsn:
        pytest.skip("SCOPE_TEST_DSN must explicitly select a disposable database")
    schema = "scope_test_" + uuid.uuid4().hex
    engine = create_async_engine(
        dsn,
        connect_args={
            "server_settings": {"search_path": f"{schema},public"},
            "timeout": 5,
        },
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
        yield engine, schema
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_backfill_resume_constraints_and_count_preservation(scope_database):
    engine, schema = scope_database
    doc_ids = [uuid.uuid4() for _ in range(4)]
    memory_id = uuid.uuid4()
    async with engine.begin() as conn:
        for doc_id in doc_ids:
            await conn.execute(
                insert(Document).values(
                    id=doc_id, title="fixture", file_type="markdown"
                )
            )
        await conn.execute(
            insert(MemoryEntity).values(canonical_name="Alice", normalized_name="alice")
        )
        await conn.execute(
            insert(MemoryUnit).values(
                id=memory_id,
                document_id=doc_ids[0],
                chunk_index=0,
                memory_index=0,
                text="fixture",
                source_text="fixture",
            )
        )
        # Recreate the actual pre-scope schema after seeding through the ORM.
        # This verifies upgrade DDL, not just create_all on an empty new schema.
        for table in TABLES:
            await conn.execute(
                text(f'ALTER TABLE "{table}" DROP COLUMN bank_id CASCADE')
            )
        for table in ("mental_models", "memory_profiles"):
            await conn.execute(text(f'ALTER TABLE "{table}" ADD PRIMARY KEY (id)'))
        await conn.execute(
            text(
                "ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_normalized_name_key UNIQUE (normalized_name)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE conversation_memory_sources ADD CONSTRAINT uq_conversation_memory_session_turn UNIQUE (session_id, turn_id)"
            )
        )
    first = await migrate_scope(engine, schema=schema, batch_size=1, max_batches=1)
    assert not first.complete
    assert first.backfilled["documents"] == 1
    result = await migrate_scope(engine, schema=schema, batch_size=1)
    assert result.complete
    assert result.counts_before == result.counts_after
    assert (await migrate_scope(engine, schema=schema)).backfilled == {
        table: 0 for table in result.backfilled
    }
    async with engine.begin() as conn:
        assert (
            await conn.scalar(
                text("SELECT bank_id FROM memory_units WHERE id = :id"),
                {"id": memory_id},
            )
            == "default-team"
        )
        await conn.execute(text("INSERT INTO memory_banks(id, name) VALUES ('b', 'B')"))
        await conn.execute(
            text("""
            INSERT INTO memory_entities(id, bank_id, canonical_name, normalized_name, entity_type, metadata_json)
            VALUES (:id, 'b', 'Alice', 'alice', 'Entity', '{}')
        """),
            {"id": uuid.uuid4()},
        )
        for bank in ("default-team", "b"):
            await conn.execute(
                text("""
                INSERT INTO mental_models(id, bank_id, name, description, summary, is_directive, source_memory_ids)
                VALUES ('overview', :bank, 'overview', '', '', false, '{}')
            """),
                {"bank": bank},
            )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE memory_units SET bank_id='b' WHERE id=:id"),
                {"id": memory_id},
            )


async def test_postgresql_matches_python_tag_truth_table(scope_database):
    engine, _ = scope_database
    expressions = [
        TagFilter(tags, mode)
        for tags in ((), ("x",), ("x", "y"))
        for mode in ("any", "all", "any_strict", "all_strict", "exact")
    ]
    expressions += [TagGroup("not", (e,)) for e in expressions]
    expressions += [
        TagGroup("and", (expressions[0], expressions[6])),
        TagGroup("or", (expressions[4], expressions[8])),
    ]
    async with engine.connect() as conn:
        for tags in (None, [], ["x"], ["y"], ["x", "y"], ["x", "x"], ["z"]):
            for expression in expressions:
                actual = await conn.scalar(
                    select(tag_predicate(literal(tags, type_=ARRAY(Text)), expression))
                )
                assert actual is expression.matches(tags), (expression, tags)


async def test_fresh_schema_migration_is_idempotent(scope_database):
    engine, schema = scope_database
    assert (await migrate_scope(engine, schema=schema)).complete


async def test_scoped_repository_read_write_and_delete(scope_database):
    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    documents = {bank: uuid.uuid4() for bank in ("a", "b")}
    memories = {bank: uuid.uuid4() for bank in documents}
    factories = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgresMemoryRepository(factories)
    embedding = [1.0] + [0.0] * 767
    async with engine.begin() as conn:
        for bank, doc_id in documents.items():
            await conn.execute(
                text("INSERT INTO memory_banks(id,name) VALUES (:bank,:bank)"),
                {"bank": bank},
            )
            await conn.execute(
                insert(Document).values(
                    id=doc_id,
                    bank_id=bank,
                    title=bank,
                    file_type="markdown",
                    status="indexed",
                    raw_text=f"scope fixture {bank}",
                    tags=["user:1"],
                )
            )
    for bank, doc_id in documents.items():
        scoped = repository.with_scope(MemoryScope(bank))
        await scoped.replace_document(
            RetainPlan(
                document_id=str(doc_id),
                title=bank,
                file_type="markdown",
                source_type="upload",
                memories=[
                    MemoryDraft(
                        id=str(memories[bank]),
                        document_id=str(doc_id),
                        chunk_index=0,
                        memory_index=0,
                        memory_type="world",
                        text=f"scope fixture {bank}",
                        source_text=f"source {bank}",
                        context="fixture",
                        embedding=embedding,
                        entities=["Alice"],
                        tags=["user:1"],
                    )
                ],
                links=[],
            )
        )
    scoped = repository.with_scope(MemoryScope("a"))
    for results in (
        await scoped.semantic_search(embedding, 10),
        await scoped.keyword_search("scope fixture", 10),
        await scoped.graph_search(["Alice"], 10),
    ):
        assert [r.id for r in results] == [str(memories["a"])]
    assert await scoped.document_state(str(documents["b"])) is None
    assert await scoped.graph_projection(str(documents["b"])) is None
    assert len(await scoped.list_backfill_candidates(force=True)) == 1
    await scoped.delete_document(str(documents["b"]))
    assert (
        len(
            await repository.with_scope(MemoryScope("b")).keyword_search(
                "scope fixture", 10
            )
        )
        == 1
    )
    restricted = repository.with_scope(
        MemoryScope("a", TagFilter(("user:2",), "all_strict"))
    )
    assert await restricted.keyword_search("scope fixture", 10) == []
    assert await restricted.document_state(str(documents["a"])) is None
    assert await repository.keyword_search("scope fixture", 10) == []
    assert (await migrate_scope(engine, schema=schema)).complete
