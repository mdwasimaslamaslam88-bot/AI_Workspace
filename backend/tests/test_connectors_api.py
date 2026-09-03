from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.connectors as connectors_module
from app.api.dependencies import get_current_user
from app.api.v1.connectors import router
from app.connectors.service import (
    ConnectorConnectionStatus,
    ConnectorExecutionError,
    ConnectorExecutionResult,
    ConnectorExecutionView,
    ConnectorNotFoundError,
    ConnectorView,
)
from app.connectors.credentials import decode_oauth2_credential
from app.db.dependencies import get_db_session
from app.models.connector import (
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecutionStatus,
    ConnectorKind,
)
from app.models.user import User


@pytest.fixture
def connectors_api(monkeypatch):
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    session = AsyncMock(spec=AsyncSession)
    user = User(id=uuid4())
    service = Mock()
    service.list_for_owner = AsyncMock(return_value=())
    service.create_for_owner = AsyncMock()
    service.update_for_owner = AsyncMock()
    service.get_for_owner = AsyncMock()
    service.revoke_for_owner = AsyncMock()
    service.execute_for_owner = AsyncMock()
    service.health_for_owner = AsyncMock()
    service.discover_for_owner = AsyncMock()
    service.disconnect_for_owner = AsyncMock()
    service.reconnect_for_owner = AsyncMock()
    service.list_executions_for_owner = AsyncMock(return_value=())
    monkeypatch.setattr(
        connectors_module,
        "_service",
        lambda request, current_session: service,
    )

    async def database_override():
        yield session

    async def user_override():
        return user

    application.dependency_overrides[get_db_session] = database_override
    application.dependency_overrides[get_current_user] = user_override
    with TestClient(application) as client:
        yield client, user, service


def _connector() -> ConnectorView:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    return ConnectorView(
        id=uuid4(),
        name="Private API",
        kind=ConnectorKind.REST,
        base_url="https://api.example.test",
        auth_kind=ConnectorAuthKind.BEARER,
        credential_configured=True,
        scopes=("read", "write"),
        path_prefixes=("/v1/",),
        health_path="/v1/health",
        enabled=True,
        connection_status=ConnectorConnectionStatus.READY,
        timeout_seconds=5,
        max_retries=1,
        rate_limit_requests_per_minute=30,
        last_health_checked_at=None,
        created_at=now,
        updated_at=now,
        revoked_at=None,
    )


def _execution(connector_id, *, error_code=None) -> ConnectorExecutionView:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    return ConnectorExecutionView(
        id=uuid4(),
        connector_id=connector_id,
        action=ConnectorAction.EXECUTE,
        method="POST",
        path="/v1/actions",
        status=(
            ConnectorExecutionStatus.FAILED
            if error_code is not None
            else ConnectorExecutionStatus.COMPLETED
        ),
        attempts=1,
        response_status_code=None if error_code else 200,
        request_body_sha256="a" * 64,
        response_body_sha256=None if error_code else "b" * 64,
        response_bytes=None if error_code else 12,
        error_code=error_code,
        started_at=now,
        completed_at=now,
        duration_ms=5,
    )


def test_create_keeps_credentials_write_only_and_passes_owner(connectors_api):
    client, user, service = connectors_api
    connector = _connector()
    service.create_for_owner.return_value = connector

    response = client.post(
        "/api/v1/connectors",
        json={
            "name": "Private API",
            "kind": "rest",
            "base_url": "https://api.example.test",
            "auth_kind": "bearer",
            "credential": "private-connector-token-123456",
            "scopes": ["read", "write"],
            "path_prefixes": ["/v1/"],
            "health_path": "/v1/health",
            "enabled": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["credential_configured"] is True
    assert "private-connector-token" not in response.text
    service.create_for_owner.assert_awaited_once()
    assert service.create_for_owner.await_args.args == (user.id,)
    assert service.create_for_owner.await_args.kwargs["credential"] == (
        "private-connector-token-123456"
    )


def test_oauth_refresh_secrets_are_write_only_and_encoded_for_encryption(connectors_api):
    client, _user, service = connectors_api
    service.create_for_owner.return_value = _connector()
    secrets = {
        "access_token": "access-token-000000000000",
        "refresh_token": "refresh-token-0000000000",
        "client_secret": "client-secret-0000000000",
    }

    response = client.post(
        "/api/v1/connectors",
        json={
            "name": "OAuth provider",
            "provider": "Example",
            "service": "Calendar",
            "kind": "rest",
            "base_url": "https://api.example.test",
            "auth_kind": "oauth2_bearer",
            "oauth2_credential": {
                **secrets,
                "client_id": "owner-client",
                "token_origin": "https://identity.example.test",
                "token_path": "/v1/oauth/token",
                "expires_at": "2026-09-03T12:00:00Z",
            },
            "scopes": ["read", "write"],
            "capabilities": ["calendar.read", "calendar.write"],
            "path_prefixes": ["/v1/"],
            "health_path": "/v1/health",
            "discovery_path": "/v1/capabilities",
            "enabled": True,
        },
    )

    assert response.status_code == 201
    for secret in secrets.values():
        assert secret not in response.text
    encoded = service.create_for_owner.await_args.kwargs["credential"]
    oauth2 = decode_oauth2_credential(encoded)
    assert oauth2 is not None
    assert oauth2.access_token == secrets["access_token"]
    assert oauth2.token_origin == "https://identity.example.test"
    assert service.create_for_owner.await_args.kwargs["capabilities"] == (
        "calendar.read",
        "calendar.write",
    )


def test_execution_returns_validated_payload_and_metadata_only_audit(connectors_api):
    client, user, service = connectors_api
    connector = _connector()
    execution = _execution(connector.id)
    service.execute_for_owner.return_value = ConnectorExecutionResult(
        execution, {"accepted": True}
    )

    response = client.post(
        f"/api/v1/connectors/{connector.id}/executions",
        json={
            "method": "POST",
            "path": "/v1/actions",
            "json_body": {"action": "sync"},
            "idempotency_key": "sync-action-00001",
        },
    )

    assert response.status_code == 201
    assert response.json()["payload"] == {"accepted": True}
    assert response.json()["execution"]["request_body_sha256"] == "a" * 64
    service.execute_for_owner.assert_awaited_once_with(
        user.id,
        connector.id,
        method="POST",
        path="/v1/actions",
        json_body={"action": "sync"},
        idempotency_key="sync-action-00001",
    )


def test_connector_failures_are_safe_and_link_to_audit(connectors_api):
    client, _user, service = connectors_api
    connector = _connector()
    failed = _execution(connector.id, error_code="connector_permission_denied")
    service.execute_for_owner.side_effect = ConnectorExecutionError(failed)

    response = client.post(
        f"/api/v1/connectors/{connector.id}/executions",
        json={"method": "POST", "path": "/v1/actions"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Connector action failed"}
    assert response.headers["X-Connector-Execution-ID"] == str(failed.id)
    assert "permission_denied" not in response.text


def test_foreign_or_missing_connector_is_not_disclosed(connectors_api):
    client, _user, service = connectors_api
    connector_id = uuid4()
    service.get_for_owner.side_effect = ConnectorNotFoundError("PRIVATE")

    response = client.get(f"/api/v1/connectors/{connector_id}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Connector not found"}
    assert "PRIVATE" not in response.text


def test_platform_catalog_is_truthful_and_owner_authenticated(connectors_api):
    client, _user, _service = connectors_api

    response = client.get("/api/v1/connectors/platform")

    assert response.status_code == 200
    payload = response.json()
    assert payload["lifecycle"] == [
        "discover",
        "configure",
        "credential_store",
        "authenticate",
        "permission_check",
        "health_check",
        "capability_discovery",
        "activate",
        "execute",
        "verify",
        "audit",
        "disconnect",
        "revoke",
        "reconnect",
    ]
    statuses = {item["id"]: item["status"] for item in payload["capabilities"]}
    assert statuses["rest"] == statuses["graphql"] == "native"
    assert statuses["websocket"] == statuses["databases"] == "adapter_required"


def test_discover_disconnect_and_reconnect_use_owner_scoped_service(connectors_api):
    client, user, service = connectors_api
    connector = _connector()
    discovered = _execution(connector.id)
    discovered = replace(discovered, action=ConnectorAction.DISCOVER)
    service.discover_for_owner.return_value = ConnectorExecutionResult(
        discovered, {"capabilities": ["read", "write"]}
    )
    service.disconnect_for_owner.return_value = replace(
        connector,
        enabled=False,
        connection_status=ConnectorConnectionStatus.DISABLED,
    )
    health = _execution(connector.id)
    health = replace(health, action=ConnectorAction.HEALTH)
    service.reconnect_for_owner.return_value = ConnectorExecutionResult(
        health, {"healthy": True}
    )

    assert client.post(f"/api/v1/connectors/{connector.id}/discover").status_code == 200
    assert client.post(f"/api/v1/connectors/{connector.id}/disconnect").status_code == 200
    assert client.post(f"/api/v1/connectors/{connector.id}/reconnect").status_code == 200
    service.discover_for_owner.assert_awaited_once_with(user.id, connector.id)
    service.disconnect_for_owner.assert_awaited_once_with(user.id, connector.id)
    service.reconnect_for_owner.assert_awaited_once_with(user.id, connector.id)
