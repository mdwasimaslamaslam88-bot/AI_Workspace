from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.api.v1.communications import router
from app.communications import CommunicationReceipt
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


def test_provider_adapter_receives_only_owner_approved_bounded_contract():
    user = _user()
    application = _application(user)
    provider = AsyncMock()

    async def accept_request(**request):
        return CommunicationReceipt(request_id=request["request_id"])

    provider.start_phone_call.side_effect = accept_request
    application.state.realtime_communication_provider = provider

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/communications/phone-calls",
            json={
                "destination": "+14155550123",
                "purpose": "Owner-approved appointment call",
                "owner_approved": True,
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "request_id": response.json()["request_id"],
        "state": "accepted_by_provider",
        "connector_execution_id": None,
    }
    provider.start_phone_call.assert_awaited_once()
    call = provider.start_phone_call.await_args.kwargs
    assert call["owner_id"] == user.id
    assert call["destination"] == "+14155550123"
    assert call["purpose"] == "Owner-approved appointment call"
    assert str(call["request_id"]) == response.json()["request_id"]


def test_communication_request_requires_owner_approval_and_e164_destination():
    application = _application(_user())
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/communications/callbacks",
            json={
                "destination": "555-0123",
                "purpose": "Callback",
                "owner_approved": False,
            },
        )

    assert response.status_code == 422
    assert "555-0123" not in response.text
