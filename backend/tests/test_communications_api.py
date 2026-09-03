from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.v1 import communications as communications_api
from app.api.v1.communications import router
from app.connectors.service import ConnectorConnectionStatus
from app.db.dependencies import get_db_session
from app.exceptions.handlers import register_exception_handlers
from app.models.user import User


def _user() -> User:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    return User(id=uuid4(), created_at=now, updated_at=now)


def _application(user: User | None = None) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)

    async def database_session():
        yield session

    application.dependency_overrides[get_db_session] = database_session
    if user is not None:
        async def current_user():
            return user

        application.dependency_overrides[get_current_user] = current_user
    return application


def test_communication_capabilities_require_authentication():
    with TestClient(_application(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/communications/capabilities")

    assert response.status_code == 401


def test_unconfigured_communication_boundary_is_explicit_and_fail_closed():
    application = _application(_user())
    with TestClient(application, raise_server_exceptions=False) as client:
        capabilities = client.get("/api/v1/communications/capabilities")
        call = client.post(
            "/api/v1/communications/phone-calls",
            json={
                "destination": "+14155550123",
                "purpose": "Owner-approved appointment call",
                "owner_approved": True,
                "connector_id": str(uuid4()),
            },
        )

    assert capabilities.status_code == 200
    assert capabilities.json()["phone_call"] == {
        "status": "external_dependency",
        "configured": False,
        "dependencies": ["telephony_provider", "owner_configuration"],
        "connector_ids": [],
    }
    assert call.status_code == 503
    assert "+14155550123" not in call.text


def test_global_provider_hook_cannot_bypass_owner_connector_and_audit():
    user = _user()
    application = _application(user)
    provider = AsyncMock()
    application.state.realtime_communication_provider = provider

    with TestClient(application, raise_server_exceptions=False) as client:
        capabilities = client.get("/api/v1/communications/capabilities")
        response = client.post(
            "/api/v1/communications/phone-calls",
            json={
                "destination": "+14155550123",
                "purpose": "Owner-approved appointment call",
                "owner_approved": True,
                "connector_id": str(uuid4()),
            },
        )

    assert capabilities.status_code == 200
    assert capabilities.json()["phone_call"]["configured"] is False
    assert response.status_code == 503
    assert "+14155550123" not in response.text
    provider.start_phone_call.assert_not_awaited()


def test_owner_connector_receipt_is_required_and_returned(monkeypatch):
    user = _user()
    connector_id = uuid4()
    execution_id = uuid4()
    service = AsyncMock()
    service.get_for_owner.return_value = SimpleNamespace(
        connection_status=ConnectorConnectionStatus.HEALTHY,
        scopes=("read", "write"),
        capabilities=("phone_call",),
    )

    async def execute_for_owner(*args, **kwargs):
        return SimpleNamespace(
            payload={
                "request_id": kwargs["json_body"]["request_id"],
                "state": "accepted_by_provider",
            },
            execution=SimpleNamespace(id=execution_id),
        )

    service.execute_for_owner.side_effect = execute_for_owner
    monkeypatch.setattr(
        communications_api,
        "_connector_service",
        lambda _request, _session: service,
    )

    with TestClient(_application(user), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/communications/phone-calls",
            json={
                "destination": "+14155550123",
                "purpose": "Owner-approved appointment call",
                "owner_approved": True,
                "connector_id": str(connector_id),
            },
        )

    assert response.status_code == 202
    assert response.json()["connector_execution_id"] == str(execution_id)
    service.get_for_owner.assert_awaited_once_with(user.id, connector_id)
    assert service.execute_for_owner.await_args.args == (user.id, connector_id)


def test_communication_request_requires_owner_approval_and_e164_destination():
    application = _application(_user())
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/communications/callbacks",
            json={
                "destination": "555-0123",
                "purpose": "Callback",
                "owner_approved": False,
                "connector_id": str(uuid4()),
            },
        )

    assert response.status_code == 422
    assert "555-0123" not in response.text
