"""Scope DDL and tag semantics against an explicitly selected disposable DB."""

import os
import uuid

import pytest
from sqlalchemy import Text, func, insert, literal, select, text
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


async def test_pi_process_replays_ack_through_production_mcp_to_postgres(
    scope_database, tmp_path, monkeypatch
):
    import asyncio
    import json
    from pathlib import Path
    import socket
    import uvicorn
    from src.agent.tkb.mcp import server as mcp_module
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.conversation_queue import (
        PostgresConversationMemoryQueue,
    )
    from src.engine.hindsight_components.conversation_service import (
        ConversationMemoryService,
    )
    from src.engine.hindsight_components.models import ConversationMemorySource
    from src.engine.hindsight_components.service import HindsightService
    from src.engine.hindsight_components.providers import ProjectHindsightProviders

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = PostgresMemoryRepository(sessions)
    service = ConversationMemoryService(
        PostgresConversationMemoryQueue(sessions),
        HindsightService(repo, ProjectHindsightProviders()),
        repo,
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "engine:\n  memory:\n    enabled: true\n    features:\n      scope: true\n      reliable_retention: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_CONFIG", str(config))
    monkeypatch.setattr(mcp_module, "_conversation_memory_service", service)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}/"
    host = uvicorn.Server(
        uvicorn.Config(mcp_module.mcp.streamable_http_app(), log_level="error")
    )
    task = asyncio.create_task(host.serve(sockets=[sock]))
    transcript = tmp_path / "pi-transcript"
    script = Path("src/extensions/pi-agent/scripts/delivery-fault-smoke.mjs").resolve()

    async def run_node(mode):
        process = await asyncio.create_subprocess_exec(
            "node",
            str(script),
            mode,
            str(transcript),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), timeout=20)
            return process.returncode, output, errors
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()

    try:
        async with asyncio.timeout(10):
            while not host.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        code, _, errors = await run_node("seed")
        assert code == 0, errors.decode()
        code, _, errors = await run_node("crash")
        assert code == 86, errors.decode()
        async with sessions() as session:
            before = list(await session.scalars(select(ConversationMemorySource)))
            assert len(before) == 1
            operation_id = before[0].operation_id
            document_id = before[0].document_id
        for _ in range(2):
            code, output, errors = await run_node("recover")
            assert code == 0, errors.decode()
            assert json.loads(output)["accepted"] == 1
        async with sessions() as session:
            after = list(await session.scalars(select(ConversationMemorySource)))
            assert len(after) == 1
            assert after[0].operation_id == operation_id
            assert after[0].document_id == document_id
            assert after[0].status == "pending"
    finally:
        host.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        sock.close()


async def test_expired_worker_process_cannot_publish_after_takeover(scope_database):
    import asyncio
    import json
    import sys
    import textwrap
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.conversation_queue import (
        PostgresConversationMemoryQueue,
    )

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = PostgresConversationMemoryQueue(sessions)
    pending = await queue.enqueue(
        session_id="process-lease", turn_id="turn", content="Source"
    )
    script = textwrap.dedent("""
        import asyncio, json, sys
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from src.engine.hindsight_components.conversation_queue import PostgresConversationMemoryQueue
        from src.engine.hindsight_components.repository import PostgresMemoryRepository
        from src.engine.hindsight_components.types import RetainPlan, RetentionLeaseLost
        async def main():
            engine = create_async_engine(sys.argv[1], connect_args={"server_settings": {"search_path": sys.argv[2]}})
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            queue = PostgresConversationMemoryQueue(sessions)
            job = (await queue.claim(lease_seconds=1))[0]
            print(json.dumps({"document_id": job.document_id, "operation_id": job.operation_id}), flush=True)
            await asyncio.to_thread(sys.stdin.readline)
            repo = PostgresMemoryRepository(sessions).with_lease(job.document_id, job.lease_token)
            try:
                await repo.replace_document(RetainPlan(document_id=job.document_id, title="late", file_type="conversation", source_type="conversation", memories=[], links=[]))
            except RetentionLeaseLost:
                print("publication_fenced", flush=True)
            else:
                raise AssertionError("expired process published memories")
            assert await queue.fail(job.document_id, "late failure", lease_token=job.lease_token) == "cancelled"
            await engine.dispose()
        asyncio.run(main())
    """)
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        os.environ["SCOPE_TEST_DSN"],
        schema,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        ready = json.loads(await asyncio.wait_for(child.stdout.readline(), timeout=15))
        assert ready["document_id"] == pending.document_id
        await asyncio.sleep(
            1.2
        )  # Real lease expiry; no mocked clock/database deadline.
        fresh = (await queue.claim(lease_seconds=60))[0]
        assert fresh.operation_id == ready["operation_id"]
        await (
            PostgresMemoryRepository(sessions)
            .with_lease(fresh.document_id, fresh.lease_token)
            .replace_document(
                RetainPlan(
                    document_id=fresh.document_id,
                    title="fresh",
                    file_type="conversation",
                    source_type="conversation",
                    memories=[],
                    links=[],
                )
            )
        )
        assert await queue.complete(fresh.document_id, lease_token=fresh.lease_token)
        child.stdin.write(b"resume\n")
        await child.stdin.drain()
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=15)
        assert child.returncode == 0, stderr.decode()
        assert b"publication_fenced" in stdout
        assert await queue.get_status(fresh.document_id) == "completed"
        assert (
            await PostgresMemoryRepository(sessions).retention_revision(
                fresh.document_id
            )
            == 1
        )
    finally:
        if child.returncode is None:
            child.terminate()
            await child.wait()


async def test_legacy_append_recovers_original_source_and_random_ids(scope_database):
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
        session.add(
            Document(
                id=doc_id,
                title="Legacy",
                file_type="text",
                raw_text="New file text that was never retained",
            )
        )
        await session.commit()
    repo = PostgresMemoryRepository(sessions)
    vector = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    await repo.replace_document(
        RetainPlan(
            document_id=str(doc_id),
            title="Legacy",
            file_type="text",
            source_type="upload",
            memories=[
                MemoryDraft(
                    id=ids[i],
                    document_id=str(doc_id),
                    chunk_index=0,
                    memory_index=i,
                    memory_type="world",
                    text="Original source" if i == 0 else "Original fact",
                    source_text="Original source",
                    context="Legacy source context",
                    embedding=vector,
                    is_source_chunk=i == 0,
                )
                for i in range(2)
            ],
            links=[],
        )
    )

    class Provider:
        prompts = []

        async def json(self, system, user, **kwargs):
            if "TEXT:\n" not in user:
                return {"observations": []}
            self.prompts.append(user)
            return {
                "facts": [
                    {
                        "text": "Original fact"
                        if "TEXT:\nOriginal source" in user
                        else "New fact",
                        "type": "world",
                    }
                ]
            }

        async def embed(self, texts, **kwargs):
            return [vector for _ in texts]

    provider = Provider()
    service = HindsightService(repo, provider)
    value = RetainInput(
        document_id=str(doc_id),
        title="Legacy",
        content="New source",
        file_type="text",
        update_mode="append",
        request_id="legacy-append",
    )
    result = await service.retain(value)
    assert result.facts == 2
    assert all("never retained" not in prompt for prompt in provider.prompts)
    async with sessions() as session:
        rows = list(
            await session.scalars(
                select(MemoryUnit).where(MemoryUnit.document_id == doc_id)
            )
        )
        assert set(ids).issubset({str(row.id) for row in rows})
    revision, snapshot = await repo.retention_content_snapshot(str(doc_id))
    assert revision == 2
    assert snapshot["content"] == "Original source\n\nNew source"
    assert await service.retain(value) == result
    assert len(provider.prompts) == 2

    from dataclasses import replace

    duplicate = replace(value, content="Original source", request_id="duplicate")
    await service.retain(duplicate)
    snapshot = (await repo.retention_content_snapshot(str(doc_id)))[1]
    original_blocks = [
        item for item in snapshot["chunks"] if item["text"] == "Original source"
    ]
    assert len({item["chunk_id"] for item in original_blocks}) == 2
    original_fact_ids = {
        memory_id
        for item in original_blocks
        for values in item["memory_ids"].values()
        for memory_id in values
    }
    assert len(original_fact_ids) == 4
    # With indistinguishable duplicate text, replace consistently keeps the first
    # saved occurrence. Adding a new occurrence must not steal that ID.
    await service.retain(
        replace(
            value,
            update_mode="replace",
            request_id="remove-duplicate",
            content="Original source",
        )
    )
    retained = (await repo.retention_content_snapshot(str(doc_id)))[1]["chunks"][0]
    assert retained["chunk_id"] == original_blocks[0]["chunk_id"]
    await service.retain(replace(duplicate, request_id="duplicate-again"))
    final_blocks = (await repo.retention_content_snapshot(str(doc_id)))[1]["chunks"]
    assert final_blocks[0]["chunk_id"] == retained["chunk_id"]
    assert final_blocks[1]["chunk_id"] not in {
        item["chunk_id"] for item in original_blocks
    }


async def test_append_replay_preserves_chunk_provenance_and_reprocess(scope_database):
    import asyncio
    from dataclasses import replace
    from datetime import datetime, timezone
    from src.engine.components.store.models import EMBEDDING_DIM
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.service import HindsightService
    from src.engine.hindsight_components.types import (
        RetainInput,
        RetentionRequestConflict,
        RetentionRevisionConflict,
    )

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessions() as session:
        session.add(
            Document(
                id=doc_id, title="Append", file_type="text", raw_text="Old raw file"
            )
        )
        await session.commit()

    class Provider:
        prompts = []
        race = False
        arrivals = 0
        barrier = asyncio.Event()

        async def json(self, system, user, **kwargs):
            if "TEXT:\n" not in user:
                return {"observations": []}
            self.prompts.append(user)
            text_value = user.split("TEXT:\n", 1)[1].split("\n\nReturn", 1)[0]
            if self.race:
                self.arrivals += 1
                if self.arrivals == 2:
                    self.barrier.set()
                await asyncio.wait_for(self.barrier.wait(), timeout=5)
            return {
                "facts": [
                    {
                        "text": text_value,
                        "type": "world",
                        "occurred_start": "yesterday",
                        "speaker_role": "user",
                    }
                ]
            }

        async def embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    provider = Provider()
    repo = PostgresMemoryRepository(sessions)
    service = HindsightService(repo, provider)
    first = RetainInput(
        document_id=str(doc_id),
        title="Append",
        content="First event",
        file_type="text",
        source_timestamp=datetime(2020, 1, 2, tzinfo=timezone.utc),
        speakers={"user": "alice"},
    )
    await service.retain(first)
    appended = replace(
        first,
        content="Second event",
        source_timestamp=datetime(2021, 2, 3, tzinfo=timezone.utc),
        speakers={"user": "bob"},
        request_id="append-1",
        update_mode="append",
    )
    result = await service.retain(appended)
    assert result.facts == 2
    assert len(provider.prompts) == 2
    assert await service.retain(appended) == result
    assert len(provider.prompts) == 2
    with pytest.raises(RetentionRequestConflict):
        await service.retain(replace(appended, content="Other event"))
    revision, snapshot = await repo.retention_content_snapshot(str(doc_id))
    assert revision == 2
    assert snapshot["content"] == "First event\n\nSecond event"
    async with sessions() as session:
        facts = list(
            (
                await session.scalars(
                    select(MemoryUnit)
                    .where(
                        MemoryUnit.document_id == doc_id,
                        MemoryUnit.is_source_chunk.is_(False),
                    )
                    .order_by(MemoryUnit.chunk_index)
                )
            ).all()
        )
        ids = [row.id for row in facts]
        assert [row.occurred_start.year for row in facts] == [2020, 2021]
        assert [row.metadata_json["speaker_id"] for row in facts] == ["alice", "bob"]
    await service.reprocess_document(str(doc_id))
    assert len(provider.prompts) == 4
    async with sessions() as session:
        facts = list(
            (
                await session.scalars(
                    select(MemoryUnit)
                    .where(
                        MemoryUnit.document_id == doc_id,
                        MemoryUnit.is_source_chunk.is_(False),
                    )
                    .order_by(MemoryUnit.chunk_index)
                )
            ).all()
        )
        assert [row.id for row in facts] == ids
        assert [row.occurred_start.year for row in facts] == [2020, 2021]
    assert (await repo.retention_content_snapshot(str(doc_id)))[1][
        "content"
    ] == snapshot["content"]
    await service.retain(
        replace(appended, content="", request_id="policy-2", policy_version=2)
    )
    assert len(provider.prompts) == 6
    assert '"source_timestamp": "2020-01-02' in provider.prompts[-2]
    assert '"source_timestamp": "2021-02-03' in provider.prompts[-1]
    assert all('"policy_version": 2' in p for p in provider.prompts[-2:])
    # Replacing the combined content must not collapse the append boundaries.
    await service.retain(replace(first, content=snapshot["content"], policy_version=2))
    assert len(provider.prompts) == 6
    await service.retain(
        replace(first, content="Updated first\n\nSecond event", policy_version=2)
    )
    assert len(provider.prompts) == 7
    current = await repo.retention_revision(str(doc_id))
    contenders = [
        replace(
            appended,
            content=f"Concurrent {i}",
            request_id=f"race-{i}",
            policy_version=2,
            expected_revision=current,
        )
        for i in range(2)
    ]
    provider.race = True
    results = await asyncio.gather(
        *(service.retain(value) for value in contenders), return_exceptions=True
    )
    provider.race = False
    assert sum(isinstance(value, RetentionRevisionConflict) for value in results) == 1
    assert await repo.retention_revision(str(doc_id)) == current + 1
    loser = next(
        i
        for i, value in enumerate(results)
        if isinstance(value, RetentionRevisionConflict)
    )
    content = (await repo.retention_content_snapshot(str(doc_id)))[1]["content"]
    assert contenders[loser].content not in content
    assert contenders[1 - loser].content in content
    # The rejected request has no committed ledger entry and can retry the new revision.
    await service.retain(replace(contenders[loser], expected_revision=current + 1))
    content = (await repo.retention_content_snapshot(str(doc_id)))[1]["content"]
    assert all(content.count(value.content) == 1 for value in contenders)


async def test_unchanged_rows_keep_external_evidence_during_reordering(scope_database):
    from dataclasses import replace
    from src.engine.components.store.models import EMBEDDING_DIM
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.models import MemoryLink
    from src.engine.hindsight_components.types import MemoryLinkDraft

    engine, schema = scope_database
    await migrate_scope(engine, schema=schema)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE memory_units DROP CONSTRAINT uq_memory_source_index, ADD CONSTRAINT uq_memory_source_index UNIQUE(document_id, chunk_index, memory_index)"
            )
        )
    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    a, b = uuid.uuid4(), uuid.uuid4()
    async with sessions() as session:
        session.add_all(
            [Document(id=d, title="source", file_type="text") for d in (a, b)]
        )
        await session.commit()
    repo = PostgresMemoryRepository(sessions)
    facts = [
        MemoryDraft(
            id=str(uuid.uuid4()),
            document_id=str(a),
            chunk_index=i,
            memory_index=0,
            memory_type="world",
            text=f"fact {i}",
            source_text=f"source {i}",
            context="",
            embedding=[0.1] * EMBEDDING_DIM,
        )
        for i in range(2)
    ]
    plan = RetainPlan(
        document_id=str(a),
        title="source",
        file_type="text",
        source_type="test",
        memories=facts,
        links=[],
    )
    await repo.replace_document(plan)
    observation = MemoryDraft(
        id=str(uuid.uuid4()),
        document_id=str(b),
        chunk_index=-1,
        memory_index=1,
        memory_type="observation",
        text="Both facts",
        source_text="Both sources",
        context="",
        embedding=[0.1] * EMBEDDING_DIM,
        source_memory_ids=[fact.id for fact in facts],
    )
    await repo.replace_document(
        RetainPlan(
            document_id=str(b),
            title="derived",
            file_type="text",
            source_type="test",
            memories=[observation],
            links=[MemoryLinkDraft(observation.id, facts[0].id, "evidence")],
        )
    )
    async with sessions() as session:
        original = (await session.get(MemoryUnit, uuid.UUID(facts[0].id))).mentioned_at
    reordered = [replace(facts[0], chunk_index=1), replace(facts[1], chunk_index=0)]
    await repo.replace_document(replace(plan, memories=reordered, expected_revision=1))
    async with sessions() as session:
        row = await session.get(MemoryUnit, uuid.UUID(facts[0].id))
        assert row.mentioned_at == original
        assert row.chunk_index == 1
        assert await session.get(MemoryUnit, uuid.UUID(observation.id)) is not None
        assert (
            await session.get(
                MemoryLink,
                (uuid.UUID(observation.id), uuid.UUID(facts[0].id), "evidence"),
            )
            is not None
        )
    await repo.replace_document(
        replace(plan, memories=[reordered[0]], expected_revision=2)
    )
    async with sessions() as session:
        assert await session.get(MemoryUnit, uuid.UUID(observation.id)) is None


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
    revision, snapshot = await repo.retention_content_snapshot(str(doc_id))
    assert revision == 2
    assert snapshot["content"] == value.content
    assert snapshot["chunks"][0]["source"]["policy_version"] == 1
    assert (await service.retain(replace(value, policy_version=2))).status == "success"
    assert provider.calls == 2
    hidden = repo.with_scope(MemoryScope(bank_id="other"))
    with pytest.raises(ValueError, match="document does not exist"):
        await hidden.retention_content_snapshot(str(doc_id))
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
    await service.retain(
        document_id=str(documents[1]),
        title="source",
        content="AC shipped the Acme project",
        file_type="text",
    )
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


async def test_consolidation_cross_retain_dedup_history_delete_and_fencing(
    scope_database,
):
    import json

    from sqlalchemy import func

    from src.engine.hindsight_components.consolidation import (
        ConsolidationOptions,
        ConsolidationWorker,
        PostgresConsolidationRepository,
    )
    from src.engine.hindsight_components.models import (
        ConsolidationFactEvent,
        ConsolidationJob,
        ConversationMemorySource,
        FactTombstone,
        ObservationEvidence,
        ObservationHistory,
        ObservationRecord,
    )

    engine, _ = scope_database
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    bank = "consolidation-bank"
    scope = MemoryScope(bank_id=bank, observation_scopes=((),))
    repository = PostgresMemoryRepository(
        sessions, scope=scope, consolidation_enabled=True
    )
    consolidation_repository = PostgresConsolidationRepository(sessions)
    options = ConsolidationOptions(semantic_dedup_enabled=False)
    documents = [uuid.uuid4() for _ in range(4)]
    facts = [uuid.uuid4() for _ in range(4)]
    vector = [0.25] * 768

    class Providers:
        async def json(self, _system, user, **_kwargs):
            payload = json.loads(user.split("\nReturn", 1)[0])
            sources = [item["id"] for item in payload["new_facts"]]
            return {
                "actions": [
                    {
                        "action": "create",
                        "text": "The user consistently prefers concise answers.",
                        "source_fact_ids": sources,
                        "change": "synthesis",
                        "reason": "repeated preference",
                    }
                ]
                if sources
                else []
            }

        async def embed(self, texts, **_kwargs):
            return [vector for _ in texts]

    worker = ConsolidationWorker(consolidation_repository, Providers(), options)
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO memory_banks(id,name) VALUES (:id,:id)"), {"id": bank}
        )
        for index, document_id in enumerate(documents):
            await connection.execute(
                insert(Document).values(
                    id=document_id,
                    bank_id=bank,
                    title=f"turn-{index}",
                    file_type="conversation",
                    status="indexed",
                    raw_text=f"concise preference {index}",
                    tags=[],
                )
            )
            await connection.execute(
                insert(ConversationMemorySource).values(
                    document_id=document_id,
                    bank_id=bank,
                    session_id="session-1",
                    turn_id=f"turn-{index}",
                    status="completed",
                )
            )

    def plan(index):
        return RetainPlan(
            document_id=str(documents[index]),
            title=f"turn-{index}",
            file_type="conversation",
            source_type="conversation",
            memories=[
                MemoryDraft(
                    id=str(facts[index]),
                    document_id=str(documents[index]),
                    chunk_index=0,
                    memory_index=1,
                    memory_type="world",
                    text=f"User requests concise answers, evidence {index}.",
                    source_text=f"concise preference {index}",
                    context="conversation",
                    embedding=vector,
                    metadata={"source_type": "conversation"},
                )
            ],
            links=[],
        )

    # A failed CAS rolls its fact event and job back with the memory publication.
    invalid = plan(0)
    invalid.expected_revision = 99
    with pytest.raises(Exception, match="revision conflict"):
        await repository.replace_document(invalid)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ConsolidationFactEvent)
            )
            == 0
        )

    # A new fact fences a worker that planned against the previous watermark.
    await repository.replace_document(plan(0))
    stale_claim = await consolidation_repository.claim(options)
    assert stale_claim is not None
    assert await consolidation_repository.claim(options) is None
    stale_read = await consolidation_repository.read_set(stale_claim, options)
    await repository.replace_document(plan(1))
    with pytest.raises(RuntimeError, match="lease lost"):
        await consolidation_repository.publish(
            stale_claim,
            stale_read,
            (),
            {},
            tokens_used=0,
            cost_microusd=0,
            options=options,
        )

    # Recovered workers consume the coalesced watermark. A repeated create is
    # converted into an update, so observation count stays one across turns.
    assert (await worker.run_once()).status == "completed"
    await repository.replace_document(plan(2))
    restarted_worker = ConsolidationWorker(
        PostgresConsolidationRepository(sessions), Providers(), options
    )
    assert (await restarted_worker.run_once()).status == "completed"

    async with sessions() as session:
        observation = await session.scalar(select(ObservationRecord))
        observation_memory = await session.get(MemoryUnit, observation.memory_id)
        assert observation.version == 2
        assert observation.freshness == "active"
        assert set(observation_memory.source_memory_ids) == set(facts[:3])
        assert (
            await session.scalar(select(func.count()).select_from(ObservationRecord))
            == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(ObservationHistory))
            == 2
        )

    # Source removal is immediately stale and cannot expose the removed evidence;
    # recomputation keeps remaining evidence and adds a new history version.
    await repository.delete_document(str(documents[0]))
    async with sessions() as session:
        observation = await session.scalar(select(ObservationRecord))
        assert observation.freshness == "stale"
        removed = await session.scalar(
            select(ObservationEvidence).where(
                ObservationEvidence.observation_id == observation.memory_id,
                ObservationEvidence.fact_id == facts[0],
            )
        )
        assert removed.active is False
        assert await session.scalar(select(FactTombstone.fact_id)) == facts[0]
    assert (await restarted_worker.run_once()).status == "completed"
    async with sessions() as session:
        observation = await session.scalar(select(ObservationRecord))
        observation_memory = await session.get(MemoryUnit, observation.memory_id)
        assert observation.version == 3
        assert observation.freshness == "active"
        assert set(observation_memory.source_memory_ids) == set(facts[1:3])
        job = await session.scalar(select(ConsolidationJob))
        assert job.processed_through == job.pending_through

    from src.engine.hindsight_components.types import RecallFilter

    details = await repository.recall_details(
        [str(observation.memory_id)], include_source_facts=True
    )
    assert details[str(observation.memory_id)]["freshness"] == "active"
    assert {
        item["id"] for item in details[str(observation.memory_id)]["source_facts"]
    } == {
        str(facts[1]),
        str(facts[2]),
    }
    expanded = await repository.expand_memory_record(str(observation.memory_id))
    assert expanded is not None
    assert "concise preference 0" not in expanded["document"]["text"]
    assert await repository.expand_memory_record(str(facts[0])) is None
    assert (
        await repository.with_scope(MemoryScope("other-bank")).expand_memory_record(
            str(observation.memory_id)
        )
        is None
    )
    observations = await repository.semantic_search(
        vector,
        10,
        source_type="conversation",
        filters=RecallFilter(memory_types=("observation",)),
    )
    assert [item.id for item in observations] == [str(observation.memory_id)]
    assert observations[0].source_type == "conversation"

    # Capacity exhaustion is durable and visible instead of silently dropping
    # the queued scope or running an unbounded model loop.
    await repository.replace_document(plan(3))

    class DistinctProviders(Providers):
        async def json(self, _system, user, **_kwargs):
            payload = json.loads(user.split("\nReturn", 1)[0])
            return {
                "actions": [
                    {
                        "action": "create",
                        "text": "A separate durable observation.",
                        "source_fact_ids": [
                            item["id"] for item in payload["new_facts"]
                        ],
                        "change": "synthesis",
                        "reason": "distinct fact",
                    }
                ]
            }

    limited = ConsolidationWorker(
        consolidation_repository,
        DistinctProviders(),
        ConsolidationOptions(observation_limit=1, semantic_dedup_enabled=False),
    )
    assert (await limited.run_once()).status == "budget_exhausted"
    async with sessions() as session:
        job = await session.scalar(select(ConsolidationJob))
        assert job.status == "budget_exhausted"
        assert job.error_msg == "observation capacity reached"


async def test_consolidation_migration_backfills_legacy_observation_and_cursor(
    scope_database,
):
    from sqlalchemy import func

    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.models import (
        ConsolidationFactEvent,
        ConsolidationJob,
        ObservationEvidence,
        ObservationRecord,
    )

    engine, schema = scope_database
    document_id = uuid.uuid4()
    fact_ids = [uuid.uuid4(), uuid.uuid4()]
    observation_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            insert(Document).values(
                id=document_id,
                title="legacy",
                file_type="markdown",
                status="indexed",
                raw_text="legacy",
            )
        )
        for index, fact_id in enumerate(fact_ids):
            await connection.execute(
                insert(MemoryUnit).values(
                    id=fact_id,
                    document_id=document_id,
                    chunk_index=0,
                    memory_index=index + 1,
                    memory_type="world",
                    text=f"legacy fact {index}",
                    source_text="legacy",
                    is_source_chunk=False,
                )
            )
        await connection.execute(
            insert(MemoryUnit).values(
                id=observation_id,
                document_id=document_id,
                chunk_index=-1,
                memory_index=1,
                memory_type="observation",
                text="  legacy   synthesis  ",
                source_text="legacy",
                source_memory_ids=fact_ids,
            )
        )
        for table in (
            "consolidation_jobs",
            "consolidation_fact_events",
            "memory_fact_tombstones",
            "observation_evidence",
            "observation_history",
            "observation_records",
        ):
            await connection.execute(text(f'DROP TABLE "{schema}"."{table}" CASCADE'))
        await connection.execute(
            text("ALTER TABLE memory_units DROP COLUMN memory_version")
        )

    await migrate_retention(engine, schema=schema)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        record = await session.get(ObservationRecord, observation_id)
        assert record.version == 1
        assert record.normalized_text == "legacy synthesis"
        assert set(
            await session.scalars(
                select(ObservationEvidence.fact_id).where(
                    ObservationEvidence.observation_id == observation_id
                )
            )
        ) == set(fact_ids)
        assert (
            await session.scalar(
                select(func.count()).select_from(ConsolidationFactEvent)
            )
            == 2
        )
        job = await session.scalar(select(ConsolidationJob))
        assert job.processed_through == 0
        assert job.pending_through > 0
    await migrate_retention(engine, schema=schema)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ConsolidationFactEvent)
            )
            == 2
        )


async def test_scoped_repository_read_write_and_delete(scope_database):
    from datetime import datetime, timezone
    from src.engine.hindsight_components.types import RecallFilter

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
    assert [
        item.id
        for item in await scoped.semantic_search(
            embedding,
            10,
            filters=RecallFilter(
                memory_types=("world",),
                tags=TagFilter(("user:1",), "all_strict"),
            ),
        )
    ] == [str(memories["a"])]
    assert (
        await scoped.semantic_search(
            embedding, 10, filters=RecallFilter(memory_types=("experience",))
        )
        == []
    )
    assert (
        await scoped.semantic_search(
            embedding,
            10,
            filters=RecallFilter(
                reference_time=datetime(2000, 1, 1, tzinfo=timezone.utc)
            ),
        )
        == []
    )
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


async def test_versioned_mental_model_refresh_and_deletion_fence(scope_database):
    from datetime import datetime, timezone
    from src.engine.components.store.retention_migration import migrate_retention
    from src.engine.hindsight_components.consolidation import (
        ConsolidationClaim,
        PostgresConsolidationRepository,
    )
    from src.engine.hindsight_components.mental_models import (
        MentalModelDefinition,
        MentalModelRefreshOptions,
        PostgresMentalModelRepository,
    )
    from src.engine.hindsight_components.models import (
        MentalModel,
        MentalModelRefreshJob,
        MentalModelVersion,
    )

    engine, schema = scope_database
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        session.add(
            MentalModel(
                id="legacy",
                bank_id="bank-a",
                name="Legacy",
                description="Legacy question",
                summary="Legacy summary",
                tags=["project:a"],
            )
        )
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE mental_model_versions"))
        await connection.execute(text("DROP TABLE mental_model_refresh_jobs"))
        for column in (
            "source_query",
            "version",
            "refresh_mode",
            "refresh_after_consolidation",
            "refresh_interval_seconds",
            "next_refresh_at",
            "last_success_at",
            "freshness",
            "error_msg",
            "evidence_watermark",
            "source_versions",
        ):
            await connection.execute(
                text(f"ALTER TABLE mental_models DROP COLUMN {column}")
            )
    await migrate_retention(engine, schema=schema)
    repository = PostgresMentalModelRepository(
        sessions, scope=MemoryScope("bank-a", TagFilter(("project:a",), "all_strict"))
    )
    legacy = await repository.get("legacy")
    assert legacy.source_query == "Legacy question"
    assert legacy.version == 1 and legacy.freshness == "active"
    created = await repository.create(
        MentalModelDefinition(
            id="overview",
            name="Project overview",
            source_query="What is the current project state?",
            tags=("project:a",),
            refresh_mode="delta",
            refresh_after_consolidation=True,
            refresh_interval_seconds=60,
        )
    )
    assert created.version == 0
    assert (
        await PostgresMentalModelRepository(sessions, scope=MemoryScope("bank-b")).get(
            "overview"
        )
        is None
    )

    document_id = uuid.uuid4()
    fact_id = uuid.uuid4()
    async with sessions() as session, session.begin():
        session.add(
            Document(
                id=document_id,
                bank_id="bank-a",
                title="project",
                file_type="markdown",
                status="indexed",
                raw_text="current",
                tags=["project:a"],
            )
        )
        session.add(
            MemoryUnit(
                id=fact_id,
                bank_id="bank-a",
                document_id=document_id,
                chunk_index=0,
                memory_index=0,
                memory_type="world",
                text="Milestone is complete",
                source_text="Milestone is complete",
                scope_tags=["project:a"],
                tags=["project:a"],
                memory_version=2,
            )
        )

    assert await repository.enqueue("overview", watermark=7)
    assert not await repository.enqueue("overview", watermark=7)
    claim = await repository.claim(MentalModelRefreshOptions())
    published = await repository.publish(
        claim,
        summary="The milestone is complete.",
        source_versions={str(fact_id): 2},
        mode="full",
        token_count=12,
        cost_microusd=3,
    )
    assert published.version == 1 and published.freshness == "active"
    async with sessions() as session:
        version = await session.get(MentalModelVersion, ("bank-a", "overview", 1))
        assert version.source_memory_ids == [fact_id]

    event_claim = ConsolidationClaim(
        bank_id="bank-a",
        scope_key="project:a",
        write_scope=("project:a",),
        lease_token=str(uuid.uuid4()),
        processed_through=7,
        claimed_through=8,
        pending_through=8,
        iterations=0,
        tokens_used=0,
        cost_microusd=0,
    )
    async with sessions() as session, session.begin():
        await PostgresConsolidationRepository._enqueue_mental_models(
            session, event_claim
        )
        await PostgresConsolidationRepository._enqueue_mental_models(
            session, event_claim
        )
    refresh_claim = await repository.claim(MentalModelRefreshOptions())
    async with sessions() as session, session.begin():
        fact = await session.get(MemoryUnit, fact_id)
        fact.state = "deleted"
        await PostgresMemoryRepository(
            sessions,
            scope=MemoryScope("bank-a"),
            consolidation_enabled=True,
        )._invalidate_mental_model_sources(session, [fact_id])
    with pytest.raises(RuntimeError, match="lease or version changed"):
        await repository.publish(
            refresh_claim,
            summary="Invalid newer summary",
            source_versions={str(fact_id): 2},
            mode="delta",
            token_count=4,
            cost_microusd=1,
        )
    await repository.fail(refresh_claim, RuntimeError("deleted"))
    current = await repository.get("overview")
    assert current.version == 1
    assert current.summary == "The milestone is complete."
    assert current.freshness == "stale"
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(MentalModelVersion))
            == 1
        )
        job = await session.get(MentalModelRefreshJob, ("bank-a", "overview"))
        assert job.status == "pending"
        assert job.error_msg == "source_deleted"
        model = await session.get(MentalModel, ("overview", "bank-a"))
        model.next_refresh_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        await session.commit()
    assert await PostgresMentalModelRepository(sessions).schedule_due() == 1
