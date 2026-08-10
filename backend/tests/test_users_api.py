from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.users as users_module
from app.db.dependencies import get_db_session
from app.main import app
from app.models.user import User


@pytest.fixture
def user_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    created_user = User(
        id=uuid4(),
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    create = AsyncMock(return_value=created_user)
    service = Mock()
    service.create = create
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(users_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, session, created_user, service_factory, create
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def get_user_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    user = User(
        id=uuid4(),
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    get_by_id = AsyncMock(return_value=user)
    service = Mock()
    service.get_by_id = get_by_id
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(users_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, session, user, service_factory, get_by_id
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_create_user_returns_201_and_exact_response_shape(user_api):
    client, session, created_user, service_factory, create = user_api

    response = client.post("/api/v1/users")

    assert response.status_code == 201
    assert response.json() == {
        "id": str(created_user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
    }
    service_factory.assert_called_once_with(session)
    create.assert_awaited_once()
    supplied_user = create.await_args.args[0]
    assert isinstance(supplied_user, User)
    assert supplied_user.id is None
    assert "owner_id" not in supplied_user.__dict__


@pytest.mark.parametrize(
    "payload",
    [
        {"id": str(uuid4())},
        {"owner_id": str(uuid4())},
    ],
)
def test_create_user_rejects_client_controlled_identity_fields(user_api, payload):
    client, _session, _created_user, service_factory, create = user_api

    response = client.post("/api/v1/users", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service_factory.assert_not_called()
    create.assert_not_awaited()


def test_create_user_service_failure_uses_existing_exception_handler(user_api):
    client, session, _created_user, service_factory, create = user_api
    error = RuntimeError("sensitive persistence detail")
    create.side_effect = error

    response = client.post("/api/v1/users")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text
    service_factory.assert_called_once_with(session)
    create.assert_awaited_once()


def test_existing_health_route_is_unaffected(user_api):
    client, _session, _created_user, service_factory, create = user_api

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    service_factory.assert_not_called()
    create.assert_not_awaited()


def test_get_user_returns_200_with_exact_response_and_no_commit(get_user_api):
    client, session, user, service_factory, get_by_id = get_user_api

    response = client.get(f"/api/v1/users/{user.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
    }
    service_factory.assert_called_once_with(session)
    get_by_id.assert_awaited_once_with(user.id)
    session.commit.assert_not_awaited()


def test_get_missing_user_returns_404_without_persistence_details(get_user_api):
    client, session, _user, service_factory, get_by_id = get_user_api
    user_id = uuid4()
    get_by_id.return_value = None

    response = client.get(f"/api/v1/users/{user_id}")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "User not found",
    }
    assert "database" not in response.text.lower()
    assert "sql" not in response.text.lower()
    service_factory.assert_called_once_with(session)
    get_by_id.assert_awaited_once_with(user_id)
    session.commit.assert_not_awaited()


def test_get_user_rejects_invalid_uuid_before_service_invocation(get_user_api):
    client, session, _user, service_factory, get_by_id = get_user_api

    response = client.get("/api/v1/users/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service_factory.assert_not_called()
    get_by_id.assert_not_awaited()
    session.commit.assert_not_awaited()
