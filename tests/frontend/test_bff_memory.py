import pytest
from fastapi import HTTPException

from src.engine.hindsight_components.memory_admin import OperationView
from src.frontend.webapp.server.routes_memory import (
    MentalModelBody,
    get_operation,
    list_operations,
    refresh_model,
    retry_operation,
)


class Service:
    def __init__(self, *, kind="conversation"):
        self.kind = kind
        self.retried = []

    async def list_memory_operations(self, **_filters):
        return [
            OperationView(
                id="operation",
                status="failed",
                stages={"retain": "failed"},
                kind=self.kind,
                subject="turn",
                session_id="session",
                turn_id="turn",
                document_id="document-id" if self.kind == "document" else None,
                error="TimeoutError",
            )
        ]

    async def get_memory_operation(self, operation_id):
        return (
            None
            if operation_id == "missing"
            else (await self.list_memory_operations())[0]
        )

    async def refresh_mental_model(self, model_id):
        if model_id == "missing":
            raise KeyError(model_id)
        return True

    async def retry_memory_operation(self, operation_id):
        self.retried.append(operation_id)
        return 1


class KnowledgeBase:
    def __init__(self):
        self.reingested = []

    async def reingest(self, document_id):
        self.reingested.append(document_id)


@pytest.mark.asyncio
async def test_operation_routes_expose_stages_without_source_text():
    rows = await list_operations(service=Service())

    assert rows[0]["session_id"] == "session"
    assert rows[0]["stages"] == {"retain": "failed"}
    assert rows[0]["kind"] == "conversation"
    assert rows[0]["subject"] == "turn"
    assert "content" not in rows[0]


@pytest.mark.asyncio
async def test_memory_routes_report_missing_resources():
    with pytest.raises(HTTPException) as operation_error:
        await get_operation("missing", service=Service())
    assert operation_error.value.status_code == 404
    with pytest.raises(HTTPException) as model_error:
        await refresh_model("missing", service=Service())
    assert model_error.value.status_code == 404


@pytest.mark.asyncio
async def test_document_operation_retry_schedules_document_reingest():
    service = Service(kind="document")
    kb = KnowledgeBase()

    result = await retry_operation("operation", service=service, kb=kb)

    assert result == {"changed": 1}
    assert kb.reingested == ["document-id"]
    assert service.retried == ["operation"]


@pytest.mark.asyncio
async def test_conversation_operation_retry_uses_memory_queue():
    service = Service()
    kb = KnowledgeBase()

    result = await retry_operation("operation", service=service, kb=kb)

    assert result == {"changed": 1}
    assert service.retried == ["operation"]
    assert kb.reingested == []


def test_model_form_rejects_invalid_refresh_interval():
    with pytest.raises(ValueError):
        MentalModelBody(
            name="Project",
            source_query="Status?",
            refresh_interval_seconds=0,
        )
