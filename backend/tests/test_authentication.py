from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.dependencies as authentication_module
from app.core.security import (
    ACCESS_TOKEN_LENGTH,
    digest_access_token,
    generate_access_token,
    is_access_token_format_valid,
)
from app.db.dependencies import get_db_session
from app.main import app
from app.models.user import User


def test_access_tokens_are_high_entropy_opaque_and_distinct():
    tokens = {generate_access_token() for _ in range(32)}

    assert len(tokens) == 32
    assert all(len(token) == ACCESS_TOKEN_LENGTH for token in tokens)
    assert all(is_access_token_format_valid(token) for token in tokens)


def test_access_token_digest_is_deterministic_sha256_without_plaintext():
    access_token = generate_access_token()

    first = digest_access_token(access_token)
    second = digest_access_token(access_token)

    assert first == second
    assert len(first) == 64
    assert first != access_token
    assert access_token not in first


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Basic credential",
        "Bearer",
        "Bearer short",
        f"Bearer {'A' * ACCESS_TOKEN_LENGTH} extra",
    ],
)
def test_missing_and_malformed_credentials_share_credential_free_401(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    service_factory = Mock()
    monkeypatch.setattr(authentication_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/users/me", headers=headers)
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    if authorization:
        assert authorization not in response.text
    service_factory.assert_not_called()
    session.commit.assert_not_awaited()


def test_valid_bearer_credential_resolves_current_user_without_commit(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    user = User(
        id=uuid4(),
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    access_token = generate_access_token()
    get_by_digest = AsyncMock(return_value=user)
    service = Mock(get_by_access_token_digest=get_by_digest)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(authentication_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
    }
    service_factory.assert_called_once_with(session)
    get_by_digest.assert_awaited_once_with(digest_access_token(access_token))
    assert access_token not in response.text
    assert "access_token_digest" not in response.text
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_unknown_bearer_credential_uses_same_credential_free_401(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    access_token = generate_access_token()
    get_by_digest = AsyncMock(return_value=None)
    service = Mock(get_by_access_token_digest=get_by_digest)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(authentication_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    assert access_token not in response.text
    service_factory.assert_called_once_with(session)
    get_by_digest.assert_awaited_once_with(digest_access_token(access_token))
    session.commit.assert_not_awaited()
