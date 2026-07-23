from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from src.core.knowledge_base import KnowledgeBase
from src.core.operations import IdempotencyConflict, OperationManager
from src.core.projector import Neo4jProjector
from src.api import mcp_server
from src.api.context import hash_api_token
from src.db.models import (
    Document,
    ExtractedEntity,
    ExtractedRelation,
    Operation,
    Team,
    TeamApiToken,
    TrustedOllamaAccount,
)
from src.db.postgres import async_session_factory

pytestmark = pytest.mark.asyncio(loop_scope="module")


class _NoopNeo4j:
    pass


class _NoopPipeline:
    pass


class _RecordingPipeline:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def process_file(self, *args) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("transient")


class _RecordingNeo4j:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def delete_document_graph(self, doc_id: str, team_id: str) -> None:
        self.calls.append(("delete", team_id, doc_id))

    async def upsert_document_node(self, *args, **kwargs) -> None:
        self.calls.append(("document", args, kwargs))

    async def upsert_entity(self, entity, source) -> None:
        self.calls.append(("entity", source.team_id, entity.name))

    async def upsert_relation(self, relation, source) -> None:
        self.calls.append(("relation", source.team_id, relation.relation_type))


async def test_document_reads_are_team_scoped() -> None:
    team_a = f"test-a-{uuid.uuid4().hex}"
    team_b = f"test-b-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    kb = KnowledgeBase(_NoopNeo4j())  # type: ignore[arg-type]

    async with async_session_factory() as session:
        session.add_all([Team(id=team_a, name=team_a), Team(id=team_b, name=team_b)])
        await session.flush()
        session.add(
            Document(
                id=doc_id,
                team_id=team_a,
                title="isolated.md",
                file_type="markdown",
                status="pending",
            )
        )
        await session.commit()
        assert await kb.get_document(session, doc_id, team_b) is None
        assert (await kb.get_document(session, doc_id, team_a))["team_id"] == team_a  # type: ignore[index]
        await session.delete(await session.get(Document, doc_id))
        await session.delete(await session.get(Team, team_a))
        await session.delete(await session.get(Team, team_b))
        await session.commit()


async def test_public_document_is_readable_but_not_writable_by_other_team() -> None:
    owner_team = f"test-public-owner-{uuid.uuid4().hex}"
    reader_team = f"test-public-reader-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    kb = KnowledgeBase(_NoopNeo4j())  # type: ignore[arg-type]

    async with async_session_factory() as session:
        session.add_all([Team(id=owner_team, name=owner_team), Team(id=reader_team, name=reader_team)])
        await session.flush()
        session.add(Document(
            id=doc_id,
            team_id=owner_team,
            title="public.md",
            file_type="markdown",
            status="indexed",
            scope="public",
        ))
        await session.commit()

        readable = await kb.get_document(session, doc_id, reader_team)
        assert readable is not None and readable["scope"] == "public"
        listed = await kb.list_documents(session, team_id=reader_team, scope="public")
        assert any(item["id"] == str(doc_id) for item in listed["items"])
        with pytest.raises(ValueError):
            await kb.edit_content(session, doc_id, "unauthorized", reader_team)

        await session.delete(await session.get(Document, doc_id))
        await session.delete(await session.get(Team, owner_team))
        await session.delete(await session.get(Team, reader_team))
        await session.commit()


async def test_operation_idempotency_is_scoped_and_conflicts_on_payload_change() -> None:
    team_id = f"test-op-{uuid.uuid4().hex}"
    manager = OperationManager(_NoopPipeline())  # type: ignore[arg-type]
    key = f"idem-{uuid.uuid4()}"
    async with async_session_factory() as session:
        session.add(Team(id=team_id, name=team_id))
        await session.flush()
        first = await manager.enqueue(
            session,
            team_id=team_id,
            operation_type="index_document",
            payload={"doc_id": str(uuid.uuid4())},
            document_id=None,
            idempotency_key=key,
            hash_payload={"content_hash": "same"},
        )
        await session.commit()
        replay = await manager.find_idempotent(
            session,
            team_id=team_id,
            idempotency_key=key,
            hash_payload={"content_hash": "same"},
        )
        assert replay is not None and replay.id == first.id

        with pytest.raises(IdempotencyConflict):
            await manager.find_idempotent(
                session,
                team_id=team_id,
                idempotency_key=key,
                hash_payload={"content_hash": "different"},
            )

        operation = await session.scalar(
            select(Operation).where(Operation.team_id == team_id)
        )
        await session.delete(operation)
        await session.delete(await session.get(Team, team_id))
        await session.commit()


async def test_projector_reads_postgres_facts_and_propagates_team_boundary() -> None:
    team_id = f"test-projector-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(Team(id=team_id, name=team_id))
        await session.flush()
        session.add(
            Document(
                id=doc_id,
                team_id=team_id,
                title="facts.md",
                file_type="markdown",
                status="indexed",
                version=1,
            )
        )
        await session.flush()
        session.add_all(
            [
                ExtractedEntity(
                    team_id=team_id,
                    doc_id=doc_id,
                    document_version=1,
                    chunk_index=0,
                    name="张伟",
                    normalized_name="张伟",
                    entity_type="Person",
                    description="物业经理",
                ),
                ExtractedRelation(
                    team_id=team_id,
                    doc_id=doc_id,
                    document_version=1,
                    chunk_index=0,
                    from_name="张伟",
                    to_name="A栋",
                    relation_type="MANAGES",
                    description="负责管理",
                ),
            ]
        )
        await session.commit()

    neo4j = _RecordingNeo4j()
    projector = Neo4jProjector(neo4j)  # type: ignore[arg-type]
    await projector._project_document(  # noqa: SLF001 - focused integration test
        {
            "team_id": team_id,
            "aggregate_id": str(doc_id),
            "aggregate_version": 1,
        }
    )
    assert ("entity", team_id, "张伟") in neo4j.calls
    assert ("relation", team_id, "MANAGES") in neo4j.calls

    async with async_session_factory() as session:
        await session.delete(await session.get(Document, doc_id))
        await session.delete(await session.get(Team, team_id))
        await session.commit()


async def test_public_document_projects_to_public_graph_namespace() -> None:
    owner_team = f"test-public-projector-{uuid.uuid4().hex}"
    doc_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(Team(id=owner_team, name=owner_team))
        await session.flush()
        session.add(Document(
            id=doc_id,
            team_id=owner_team,
            title="public-facts.md",
            file_type="markdown",
            status="indexed",
            scope="public",
            version=1,
        ))
        await session.flush()
        session.add(ExtractedEntity(
            team_id=owner_team,
            doc_id=doc_id,
            document_version=1,
            chunk_index=0,
            name="公共设施",
            normalized_name="公共设施",
            entity_type="Facility",
            description="所有团队可见",
        ))
        await session.commit()

    neo4j = _RecordingNeo4j()
    projector = Neo4jProjector(neo4j)  # type: ignore[arg-type]
    await projector._project_document({  # noqa: SLF001
        "team_id": owner_team,
        "aggregate_id": str(doc_id),
        "aggregate_version": 1,
    })
    assert ("entity", "public", "公共设施") in neo4j.calls
    assert ("delete", owner_team, str(doc_id)) in neo4j.calls

    async with async_session_factory() as session:
        await session.delete(await session.get(Document, doc_id))
        await session.delete(await session.get(Team, owner_team))
        await session.commit()


async def test_operation_worker_claims_completes_and_retries_persistently() -> None:
    team_id = f"test-worker-{uuid.uuid4().hex}"
    success_pipeline = _RecordingPipeline()
    manager = OperationManager(success_pipeline)  # type: ignore[arg-type]
    async with async_session_factory() as session:
        session.add(Team(id=team_id, name=team_id))
        await session.flush()
        operation = await manager.enqueue(
            session,
            team_id=team_id,
            operation_type="index_document",
            payload={
                "doc_id": str(uuid.uuid4()),
                "file_path": "unused.md",
                "title": "unused.md",
                "file_type": "markdown",
            },
            document_id=None,
        )
        operation_id = operation.id
        await session.commit()

    claimed = await manager._claim(team_id)  # noqa: SLF001
    assert claimed is not None and claimed["id"] == operation_id
    await manager._execute(claimed)  # noqa: SLF001
    assert success_pipeline.calls == 1

    async with async_session_factory() as session:
        completed = await session.get(Operation, operation_id)
        assert completed.status == "succeeded"
        await session.delete(completed)

        failing = await manager.enqueue(
            session,
            team_id=team_id,
            operation_type="index_document",
            payload={
                "doc_id": str(uuid.uuid4()),
                "file_path": "unused.md",
                "title": "unused.md",
                "file_type": "markdown",
            },
            document_id=None,
        )
        failing_id = failing.id
        await session.commit()

    failing_manager = OperationManager(_RecordingPipeline(fail=True))  # type: ignore[arg-type]
    claimed = await failing_manager._claim(team_id)  # noqa: SLF001
    assert claimed is not None and claimed["id"] == failing_id
    await failing_manager._execute(claimed)  # noqa: SLF001

    async with async_session_factory() as session:
        retrying = await session.get(Operation, failing_id)
        assert retrying.status == "retry_wait"
        assert retrying.next_retry_at is not None
        await session.delete(retrying)
        await session.delete(await session.get(Team, team_id))
        await session.commit()


async def test_mcp_bearer_token_resolves_username_team_and_viewer_permission(monkeypatch) -> None:
    team_id = f"test-mcp-user-{uuid.uuid4().hex}"
    raw_token = f"tkb_test_{uuid.uuid4().hex}"
    token_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(Team(id=team_id, name=team_id))
        await session.flush()
        session.add(TeamApiToken(
            id=token_id,
            team_id=team_id,
            name="Ollama viewer",
            subject="ollama-zhangsan",
            token_hash=hash_api_token(raw_token),
            token_prefix=raw_token[:12],
            roles=["viewer"],
        ))
        await session.commit()

    fake_context = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers={"authorization": f"Bearer {raw_token}"})
        )
    )
    monkeypatch.setattr(mcp_server.mcp, "get_context", lambda: fake_context)
    principal = await mcp_server._get_mcp_principal()  # noqa: SLF001
    assert principal.team_id == team_id
    assert principal.subject == "ollama-zhangsan"
    assert principal.roles == ("viewer",)
    with pytest.raises(RuntimeError, match="只读权限"):
        mcp_server._require_mcp_write(principal)  # noqa: SLF001

    async with async_session_factory() as session:
        await session.delete(await session.get(TeamApiToken, token_id))
        await session.delete(await session.get(Team, team_id))
        await session.commit()


async def test_trusted_ollama_account_needs_no_token_and_inherits_public_documents(monkeypatch) -> None:
    username = f"ollama-{uuid.uuid4().hex}"
    team_a = f"test-ollama-a-{uuid.uuid4().hex}"
    team_b = f"test-ollama-b-{uuid.uuid4().hex}"
    account_a_id = uuid.uuid4()
    account_b_id = uuid.uuid4()
    public_doc_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add_all([Team(id=team_a, name=team_a), Team(id=team_b, name=team_b)])
        await session.flush()
        session.add_all([
            TrustedOllamaAccount(
                id=account_a_id, team_id=team_a, username=username,
                display_name="Ollama A", roles=["viewer"],
            ),
            TrustedOllamaAccount(
                id=account_b_id, team_id=team_b, username=username,
                display_name="Ollama B", roles=["member"],
            ),
            Document(
                id=public_doc_id,
                team_id=team_b,
                title="ollama-public.md",
                file_type="markdown",
                raw_text="shared",
                status="indexed",
                scope="public",
            ),
        ])
        await session.commit()

    request = SimpleNamespace(
        headers={"x-tkb-ollama-user": username, "x-tkb-team": team_a},
        query_params={},
        client=SimpleNamespace(host="192.0.2.10"),
    )
    fake_context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    monkeypatch.setattr(mcp_server.mcp, "get_context", lambda: fake_context)
    with pytest.raises(RuntimeError, match="不来自受信网络"):
        await mcp_server._get_mcp_principal()  # noqa: SLF001

    request.client.host = "127.0.0.1"
    principal = await mcp_server._get_mcp_principal()  # noqa: SLF001
    assert principal.team_id == team_a
    assert principal.subject == username
    assert principal.auth_source == "ollama-account"
    assert principal.accessible_team_ids == (team_a, team_b)
    selected = await mcp_server._principal_for_knowledge_base(principal, team_b)  # noqa: SLF001
    assert selected.team_id == team_b
    assert selected.roles == ("member",)
    with pytest.raises(RuntimeError, match="无权访问"):
        await mcp_server._principal_for_knowledge_base(principal, "unknown-team")  # noqa: SLF001

    knowledge_bases = await mcp_server.list_knowledge_bases()
    assert knowledge_bases["includes_public_documents"] is True
    assert {item["id"] for item in knowledge_bases["items"]} == {team_a, team_b}
    assert all(item["public_document_count"] >= 1 for item in knowledge_bases["items"])
    assert all(item["document_count"] >= 1 for item in knowledge_bases["items"])

    async with async_session_factory() as session:
        await session.delete(await session.get(Document, public_doc_id))
        await session.delete(await session.get(TrustedOllamaAccount, account_a_id))
        await session.delete(await session.get(TrustedOllamaAccount, account_b_id))
        await session.delete(await session.get(Team, team_a))
        await session.delete(await session.get(Team, team_b))
        await session.commit()
