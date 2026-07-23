"""Outbox consumer that maintains the rebuildable Neo4j projection."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.config import settings
from src.db.models import (
    Document,
    DocumentRelation,
    ExtractedEntity,
    ExtractedRelation,
    OutboxEvent,
)
from src.db.neo4j_client import EntityData, EntitySource, Neo4jClient, RelationData
from src.db.postgres import async_session_factory

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Neo4jProjector:
    def __init__(self, neo4j: Neo4jClient) -> None:
        self._neo4j = neo4j
        self._worker_id = f"projector-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="tkb-neo4j-projector")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = await self._claim()
                if event is None:
                    await asyncio.sleep(settings.projector_poll_interval)
                    continue
                await self._project(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Neo4j projector loop failed")
                await asyncio.sleep(settings.projector_poll_interval)

    async def _claim(self) -> dict[str, Any] | None:
        now = _utcnow()
        async with async_session_factory() as session:
            event = await session.scalar(
                select(OutboxEvent)
                .where(
                    or_(
                        and_(
                            OutboxEvent.status == "pending",
                            or_(OutboxEvent.next_retry_at.is_(None), OutboxEvent.next_retry_at <= now),
                        ),
                        and_(OutboxEvent.status == "processing", OutboxEvent.lease_expires_at < now),
                    )
                )
                .order_by(OutboxEvent.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not event:
                return None
            event.status = "processing"
            event.worker_id = self._worker_id
            event.lease_expires_at = now + timedelta(seconds=settings.operation_lease_seconds)
            event.attempt_count += 1
            await session.commit()
            return {
                "id": event.id,
                "team_id": event.team_id,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "aggregate_version": event.aggregate_version,
                "payload": event.payload,
            }

    async def _project(self, event: dict[str, Any]) -> None:
        try:
            if event["event_type"] == "document_graph_delete_requested":
                projection_team_id = event.get("payload", {}).get("projection_team_id", event["team_id"])
                await self._neo4j.delete_document_graph(event["aggregate_id"], projection_team_id)
                if projection_team_id == "public":
                    await self._neo4j.delete_document_graph(event["aggregate_id"], event["team_id"])
            elif event["event_type"] == "document_graph_upsert_requested":
                await self._project_document(event)
            else:
                raise ValueError(f"未知 outbox event: {event['event_type']}")
        except Exception as exc:
            await self._mark_retry(event, exc)
            return
        await self._mark_processed(event)

    async def _project_document(self, event: dict[str, Any]) -> None:
        doc_id = uuid.UUID(event["aggregate_id"])
        team_id = event["team_id"]
        version = event["aggregate_version"]
        async with async_session_factory() as session:
            document = await session.scalar(
                select(Document).where(Document.id == doc_id, Document.team_id == team_id)
            )
            # A newer document version makes this event stale. The newer event
            # will perform the complete replacement projection.
            if not document or document.version != version:
                return
            entities = (
                await session.scalars(
                    select(ExtractedEntity).where(
                        ExtractedEntity.team_id == team_id,
                        ExtractedEntity.doc_id == doc_id,
                        ExtractedEntity.document_version == version,
                    )
                )
            ).all()
            relations = (
                await session.scalars(
                    select(ExtractedRelation).where(
                        ExtractedRelation.team_id == team_id,
                        ExtractedRelation.doc_id == doc_id,
                        ExtractedRelation.document_version == version,
                    )
                )
            ).all()
            doc_relations = (
                await session.scalars(
                    select(DocumentRelation).where(
                        DocumentRelation.team_id == team_id,
                        DocumentRelation.source_doc_id == doc_id,
                    )
                )
            ).all()
            target_ids = [row.target_doc_id for row in doc_relations]
            target_titles = {}
            if target_ids:
                target_rows = (
                    await session.execute(
                        select(Document.id, Document.title, Document.scope).where(
                            Document.id.in_(target_ids), Document.team_id == team_id
                        )
                    )
                ).all()
                target_titles = {row.id: row.title for row in target_rows}
                if document.scope == "public":
                    public_target_ids = {row.id for row in target_rows if row.scope == "public"}
                    doc_relations = [row for row in doc_relations if row.target_doc_id in public_target_ids]

        projection_team_id = "public" if document.scope == "public" else team_id
        await self._neo4j.delete_document_graph(str(doc_id), projection_team_id)
        if projection_team_id == "public":
            # Remove the legacy owner-team projection created before public
            # documents had a separate graph namespace.
            await self._neo4j.delete_document_graph(str(doc_id), team_id)
        await self._neo4j.upsert_document_node(
            str(doc_id),
            document.title,
            document.file_type,
            document.overview,
            projection_team_id,
            version,
        )
        for fact in entities:
            await self._neo4j.upsert_entity(
                EntityData(fact.name, fact.entity_type, fact.description),
                EntitySource(str(doc_id), fact.chunk_index, document.title, projection_team_id),
            )
        for fact in relations:
            await self._neo4j.upsert_relation(
                RelationData(fact.from_name, fact.to_name, fact.relation_type, fact.description),
                EntitySource(str(doc_id), fact.chunk_index, document.title, projection_team_id),
            )
        for relation in doc_relations:
            await self._neo4j.ensure_document_node(
                str(relation.target_doc_id),
                target_titles.get(relation.target_doc_id, ""),
                projection_team_id,
            )
            await self._neo4j.create_doc_relation(
                str(doc_id),
                str(relation.target_doc_id),
                relation.relation_type,
                relation.reason,
                projection_team_id,
            )

    async def _mark_processed(self, event: dict[str, Any]) -> None:
        async with async_session_factory() as session:
            row = await session.get(OutboxEvent, event["id"])
            if not row:
                return
            row.status = "processed"
            row.processed_at = _utcnow()
            row.worker_id = None
            row.lease_expires_at = None
            if row.event_type == "document_graph_upsert_requested":
                document = await session.scalar(
                    select(Document).where(
                        Document.id == uuid.UUID(row.aggregate_id),
                        Document.team_id == row.team_id,
                        Document.version == row.aggregate_version,
                    )
                )
                if document:
                    document.graph_status = "ready"
            await session.commit()

    async def _mark_retry(self, event: dict[str, Any], exc: Exception) -> None:
        async with async_session_factory() as session:
            row = await session.get(OutboxEvent, event["id"])
            if not row:
                return
            row.error_message = str(exc)
            row.worker_id = None
            row.lease_expires_at = None
            if row.attempt_count < settings.worker_max_attempts:
                row.status = "pending"
                row.next_retry_at = _utcnow() + timedelta(seconds=2 ** row.attempt_count)
            else:
                row.status = "failed"
                document = await session.scalar(
                    select(Document).where(
                        Document.id == uuid.UUID(row.aggregate_id), Document.team_id == row.team_id
                    )
                )
                if document:
                    document.graph_status = "failed"
            await session.commit()

    async def rebuild(
        self, session: AsyncSession, team_id: str, document_id: uuid.UUID | None = None
    ) -> int:
        stmt = select(Document).where(Document.team_id == team_id)
        if document_id:
            stmt = stmt.where(Document.id == document_id)
        documents = (await session.scalars(stmt)).all()
        count = 0
        for document in documents:
            document.graph_status = "pending"
            event = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.team_id == team_id,
                    OutboxEvent.aggregate_type == "document",
                    OutboxEvent.aggregate_id == str(document.id),
                    OutboxEvent.aggregate_version == document.version,
                    OutboxEvent.event_type == "document_graph_upsert_requested",
                )
            )
            if event:
                event.status = "pending"
                event.attempt_count = 0
                event.next_retry_at = None
                event.error_message = None
                event.processed_at = None
            else:
                session.add(
                    OutboxEvent(
                        team_id=team_id,
                        aggregate_type="document",
                        aggregate_id=str(document.id),
                        aggregate_version=document.version,
                        event_type="document_graph_upsert_requested",
                        payload={"document_id": str(document.id), "rebuild": True},
                    )
                )
            count += 1
        await session.commit()
        return count

    async def reconcile(self, session: AsyncSession, team_id: str) -> dict[str, Any]:
        documents = (await session.scalars(select(Document).where(Document.team_id == team_id))).all()
        drift: list[dict[str, Any]] = []
        for document in documents:
            projection_team_id = "public" if document.scope == "public" else team_id
            projected = await self._neo4j.get_document_projection_version(
                str(document.id), projection_team_id
            )
            if projected != document.version:
                drift.append(
                    {
                        "document_id": str(document.id),
                        "postgres_version": document.version,
                        "neo4j_version": projected,
                    }
                )
        return {"team_id": team_id, "checked": len(documents), "drift": drift}
