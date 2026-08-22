from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.tools as tools_module
from app.api.dependencies import get_current_user
from app.api.v1.tools import router
from app.db.dependencies import get_db_session
from app.models.tool import ToolExecutionStatus
from app.models.user import User
from app.services.tool import ToolExecutionRecord, ToolNotFoundError


@pytest.fixture
def tools_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.execute_for_owner = AsyncMock()
    monkeypatch.setattr(
        tools_module,
        "_service",
        lambda request, current_session: service,
    )

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, user, service


def _record():
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    return ToolExecutionRecord(
        id=uuid4(),
        conversation_id=None,
        tool_name="calculator",
        permission="utility",
        status=ToolExecutionStatus.COMPLETED,
        initiator="explicit_user",
        arguments={"expression": "6*7"},
        result={"value": 42},
        error_code=None,
        started_at=now,
        completed_at=now,
        duration_ms=1,
    )


def test_registry_exposes_schema_permission_timeout_and_output_bound(tools_api):
    client, _user, _service = tools_api

    response = client.get("/api/v1/tools")

    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["name"] for item in items} == {
        "calculator",
        "local_time",
        "document_search",
        "conversation_search",
        "memory_search",
    }
    assert all(
        item["input_schema"]["additionalProperties"] is False
        and item["timeout_seconds"] <= 5
        and item["max_output_characters"] <= 12_000
        for item in items
    )


def test_execute_passes_authenticated_owner_and_returns_safe_result(tools_api):
    client, user, service = tools_api
    record = _record()
    service.execute_for_owner.return_value = record

    response = client.post(
        "/api/v1/tools/calculator/executions",
        json={"arguments": {"expression": "6*7"}},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert response.json()["result"] == {"value": 42}
    assert "owner_id" not in response.text
    service.execute_for_owner.assert_awaited_once_with(
        user.id,
        "calculator",
        {"expression": "6*7"},
        conversation_id=None,
    )


def test_unknown_tool_and_foreign_details_are_safe(tools_api):
    client, _user, service = tools_api
    service.execute_for_owner.side_effect = ToolNotFoundError("PRIVATE_SENTINEL")

    response = client.post(
        "/api/v1/tools/shell/executions",
        json={"arguments": {"command": "id"}},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Tool not found"}
    assert "PRIVATE_SENTINEL" not in response.text


def test_history_is_owner_scoped_and_bounded(tools_api):
    client, user, service = tools_api
    service.list_for_owner.return_value = (_record(),)

    response = client.get("/api/v1/tools/executions?limit=1")

    assert response.status_code == 200
    assert response.json()["items"][0]["tool_name"] == "calculator"
    service.list_for_owner.assert_awaited_once_with(user.id, limit=1)
