from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.workflows as workflows_module
from app.api.dependencies import get_current_user
from app.api.v1.workflows import router
from app.db.dependencies import get_db_session
from app.models.user import User
from app.models.workflow import WorkflowStatus
from app.services.workflow import (
    WorkflowNotFoundError,
    WorkflowRecord,
    WorkflowStepRecord,
)


@pytest.fixture
def workflow_api(monkeypatch):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.create_for_owner = AsyncMock()
    service.get_for_owner = AsyncMock(return_value=None)
    runner = Mock()
    runner.start_for_owner = AsyncMock()
    runner.cancel_for_owner = AsyncMock()
    app.state.workflow_runner = runner
    monkeypatch.setattr(
        workflows_module, "WorkflowService", Mock(return_value=service)
    )

    async def database_override():
        yield session

    async def user_override():
        return user

    app.dependency_overrides[get_db_session] = database_override
    app.dependency_overrides[get_current_user] = user_override
    with TestClient(app) as client:
        yield client, user, session, service, runner


def _record(*, status=WorkflowStatus.PENDING):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    terminal = status in {
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
        WorkflowStatus.TIMED_OUT,
    }
    return WorkflowRecord(
        id=uuid4(),
        name="Research",
        status=status,
        step_count=1,
        current_step_position=(
            None if status is WorkflowStatus.PENDING else 1
        ),
        cancel_requested=status is WorkflowStatus.CANCELLED,
        result={"steps": []} if status is WorkflowStatus.COMPLETED else None,
        error_code=(
            "workflow_cancelled"
            if status is WorkflowStatus.CANCELLED
            else None
        ),
        created_at=now,
        updated_at=now,
        started_at=(None if status is WorkflowStatus.PENDING else now),
        completed_at=now if terminal else None,
        steps=(
            WorkflowStepRecord(
                id=uuid4(),
                position=1,
                tool_name="document_search",
                permission="personal_documents_read",
                arguments={"query": "Apollo", "limit": 4},
                status=WorkflowStatus.PENDING,
                tool_execution_id=None,
                result=None,
                error_code=None,
                started_at=None,
                completed_at=None,
                duration_ms=None,
            ),
        ),
    )


def test_create_passes_authenticated_owner_and_exact_bounded_steps(workflow_api):
    client, user, _session, service, _runner = workflow_api
    record = _record()
    service.create_for_owner.return_value = record

    response = client.post(
        "/api/v1/workflows",
        json={
            "name": "Research",
            "steps": [
                {
                    "tool_name": "document_search",
                    "arguments": {"query": "Apollo", "limit": 4},
                }
            ],
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert "owner_id" not in response.text
    draft = service.create_for_owner.await_args.args[2][0]
    assert service.create_for_owner.await_args.args[:2] == (user.id, "Research")
    assert draft.tool_name == "document_search"
    assert draft.arguments == {"query": "Apollo", "limit": 4}


def test_start_and_cancel_delegate_only_with_authenticated_owner(workflow_api):
    client, user, session, _service, runner = workflow_api
    pending = _record()
    cancelled = replace(
        _record(status=WorkflowStatus.CANCELLED), id=pending.id
    )
    runner.start_for_owner.return_value = pending
    runner.cancel_for_owner.return_value = cancelled

    started = client.post(f"/api/v1/workflows/{pending.id}/start")
    stopped = client.delete(f"/api/v1/workflows/{pending.id}")

    assert started.status_code == 202
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled"
    runner.start_for_owner.assert_awaited_once_with(user.id, pending.id)
    runner.cancel_for_owner.assert_awaited_once_with(user.id, pending.id)
    assert session.rollback.await_count == 2


def test_list_and_get_are_owner_scoped(workflow_api):
    client, user, _session, service, _runner = workflow_api
    record = _record()
    service.list_for_owner.return_value = (record,)
    service.get_for_owner.return_value = record

    listed = client.get("/api/v1/workflows?limit=1")
    fetched = client.get(f"/api/v1/workflows/{record.id}")

    assert listed.status_code == 200
    assert fetched.status_code == 200
    service.list_for_owner.assert_awaited_once_with(user.id, limit=1)
    service.get_for_owner.assert_awaited_once_with(user.id, record.id)


def test_foreign_workflow_returns_fixed_not_found(workflow_api):
    client, _user, _session, _service, runner = workflow_api
    runner.start_for_owner.side_effect = WorkflowNotFoundError("PRIVATE_SENTINEL")

    response = client.post(f"/api/v1/workflows/{uuid4()}/start")

    assert response.status_code == 404
    assert response.json() == {"detail": "Workflow not found"}
    assert "PRIVATE_SENTINEL" not in response.text


def test_request_schema_rejects_more_than_eight_steps_before_service(workflow_api):
    client, _user, _session, service, _runner = workflow_api

    response = client.post(
        "/api/v1/workflows",
        json={
            "steps": [
                {"tool_name": "calculator", "arguments": {"expression": "1"}}
                for _ in range(9)
            ]
        },
    )

    assert response.status_code == 422
    service.create_for_owner.assert_not_awaited()
