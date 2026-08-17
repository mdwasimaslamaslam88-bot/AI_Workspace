from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.dependencies as authentication_module
import app.api.v1.users as users_module
import app.core.security as security_module
from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.main import app
from app.models.user import User


_PROVISIONING_TOKEN = "P" * 43
_PROVISIONING_DIGEST = security_module.digest_access_token(_PROVISIONING_TOKEN)
_PROVISIONING_HEADERS = {
    "X-User-Provisioning-Token": _PROVISIONING_TOKEN,
}


@pytest.fixture
def user_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    created_user = User(
        id=uuid4(),
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    access_token = "A" * 43
    provision = AsyncMock(return_value=(created_user, access_token))
    service = Mock()
    service.provision_with_access_token = provision
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(users_module, "UserService", service_factory)
    monkeypatch.setattr(
        users_module.settings,
        "USER_PROVISIONING_TOKEN_DIGEST",
        _PROVISIONING_DIGEST,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield (
                client,
                session,
                created_user,
                access_token,
                service_factory,
                provision,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def get_user_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    access_token = "L" * 43
    user = User(
        id=uuid4(),
        access_token_digest=security_module.digest_access_token(access_token),
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    get_by_digest = AsyncMock(return_value=user)
    authentication_service = Mock()
    authentication_service.get_by_access_token_digest = get_by_digest
    authentication_service_factory = Mock(return_value=authentication_service)
    route_service_factory = Mock()
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        authentication_service_factory,
    )
    monkeypatch.setattr(users_module, "UserService", route_service_factory)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield (
                client,
                session,
                user,
                access_token,
                authentication_service_factory,
                get_by_digest,
                route_service_factory,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)


def test_create_user_returns_201_and_exact_response_shape(user_api):
    (
        client,
        session,
        created_user,
        access_token,
        service_factory,
        provision,
    ) = user_api

    response = client.post(
        "/api/v1/users",
        headers=_PROVISIONING_HEADERS,
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(created_user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
        "access_token": access_token,
        "token_type": "bearer",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.text.count(access_token) == 1
    assert "access_token_digest" not in response.text
    assert _PROVISIONING_TOKEN not in response.text
    assert _PROVISIONING_DIGEST not in response.text
    service_factory.assert_called_once_with(session)
    provision.assert_awaited_once_with()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": str(uuid4())},
        {"user_id": str(uuid4())},
        {"owner_id": str(uuid4())},
        {"access_token": "client-controlled"},
        {"token": "client-controlled"},
        {"digest": "client-controlled"},
        {"access_token_digest": "client-controlled"},
        {"unknown": "client-controlled"},
    ],
)
def test_create_user_rejects_client_controlled_identity_fields(user_api, payload):
    (
        client,
        _session,
        _created_user,
        _access_token,
        service_factory,
        provision,
    ) = user_api

    response = client.post(
        "/api/v1/users",
        headers=_PROVISIONING_HEADERS,
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service_factory.assert_not_called()
    provision.assert_not_awaited()


def test_create_user_service_failure_uses_existing_exception_handler(user_api):
    (
        client,
        session,
        _created_user,
        access_token,
        service_factory,
        provision,
    ) = user_api
    error = RuntimeError("sensitive persistence detail")
    provision.side_effect = error

    response = client.post(
        "/api/v1/users",
        headers=_PROVISIONING_HEADERS,
    )

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "sensitive persistence detail" not in response.text
    assert access_token not in response.text
    service_factory.assert_called_once_with(session)
    provision.assert_awaited_once_with()


def test_existing_health_route_is_unaffected(user_api):
    (
        client,
        _session,
        _created_user,
        _access_token,
        service_factory,
        provision,
    ) = user_api

    response = client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}
    service_factory.assert_not_called()
    provision.assert_not_awaited()


def test_get_me_uses_current_user_dependency_and_safe_response_shape():
    user = User(
        id=uuid4(),
        access_token_digest="b" * 64,
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/users/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
    }
    assert "access_token" not in response.text
    assert "access_token_digest" not in response.text


def test_get_me_is_matched_before_uuid_route_and_cannot_use_user_id():
    user_id = uuid4()
    session = AsyncMock(spec=AsyncSession)

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get(f"/api/v1/users/me?user_id={user_id}")
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == (
        "Invalid authentication credentials"
    )
    assert response.headers["WWW-Authenticate"] == "Bearer"
    session.commit.assert_not_awaited()


def test_get_user_returns_loaded_current_user_without_second_lookup_or_side_effect(
    get_user_api,
):
    (
        client,
        session,
        user,
        access_token,
        authentication_service_factory,
        get_by_digest,
        route_service_factory,
    ) = get_user_api

    response = client.get(
        f"/api/v1/users/{user.id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
    }
    assert access_token not in response.text
    assert user.access_token_digest not in response.text
    assert _PROVISIONING_TOKEN not in response.text
    assert _PROVISIONING_DIGEST not in response.text
    authentication_service_factory.assert_called_once_with(session)
    get_by_digest.assert_awaited_once_with(user.access_token_digest)
    route_service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_get_user_nonself_ids_share_exact_404_without_route_lookup(get_user_api):
    (
        client,
        session,
        _user,
        access_token,
        authentication_service_factory,
        get_by_digest,
        route_service_factory,
    ) = get_user_api
    first_nonself_id = uuid4()
    second_nonself_id = uuid4()
    headers = {"Authorization": f"Bearer {access_token}"}

    responses = [
        client.get(f"/api/v1/users/{first_nonself_id}", headers=headers),
        client.get(f"/api/v1/users/{second_nonself_id}", headers=headers),
    ]

    assert [response.status_code for response in responses] == [404, 404]
    assert [response.json()["error"] for response in responses] == [
        {"code": "HTTP_ERROR", "message": "User not found"},
        {"code": "HTTP_ERROR", "message": "User not found"},
    ]
    for response in responses:
        assert access_token not in response.text
        assert _PROVISIONING_TOKEN not in response.text
        assert _PROVISIONING_DIGEST not in response.text
        assert "database" not in response.text.lower()
        assert "sql" not in response.text.lower()
    assert authentication_service_factory.call_count == 2
    assert get_by_digest.await_count == 2
    route_service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_get_user_identity_overrides_cannot_change_self_only_authorization(
    get_user_api,
):
    (
        client,
        _session,
        user,
        access_token,
        _authentication_service_factory,
        _get_by_digest,
        route_service_factory,
    ) = get_user_api
    foreign_id = uuid4()
    headers = {"Authorization": f"Bearer {access_token}"}
    overrides = {
        "user_id": str(user.id),
        "owner_id": str(user.id),
        "token": access_token,
        "digest": user.access_token_digest,
    }

    denied = client.request(
        "GET",
        f"/api/v1/users/{foreign_id}",
        headers=headers,
        params=overrides,
        json=overrides,
    )
    allowed = client.request(
        "GET",
        f"/api/v1/users/{user.id}",
        headers=headers,
        params={key: str(foreign_id) for key in overrides},
        json={key: str(foreign_id) for key in overrides},
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["message"] == "User not found"
    assert allowed.status_code == 200
    assert allowed.json()["id"] == str(user.id)
    route_service_factory.assert_not_called()


def test_get_user_rejects_authenticated_malformed_uuid_before_route_lookup(
    get_user_api,
):
    (
        client,
        session,
        _user,
        access_token,
        _authentication_service_factory,
        _get_by_digest,
        route_service_factory,
    ) = get_user_api

    response = client.get(
        "/api/v1/users/not-a-uuid",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    route_service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic credential"},
        {"Authorization": "Bearer short"},
        _PROVISIONING_HEADERS,
    ],
)
def test_get_user_requires_uniform_bearer_authentication(get_user_api, headers):
    (
        client,
        session,
        user,
        _access_token,
        authentication_service_factory,
        get_by_digest,
        route_service_factory,
    ) = get_user_api

    response = client.get(f"/api/v1/users/{user.id}", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    authentication_service_factory.assert_not_called()
    get_by_digest.assert_not_awaited()
    route_service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_get_user_unknown_bearer_uses_same_credential_free_401(get_user_api):
    (
        client,
        session,
        user,
        access_token,
        authentication_service_factory,
        get_by_digest,
        route_service_factory,
    ) = get_user_api
    get_by_digest.return_value = None

    response = client.get(
        f"/api/v1/users/{user.id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    assert access_token not in response.text
    authentication_service_factory.assert_called_once_with(session)
    get_by_digest.assert_awaited_once_with(user.access_token_digest)
    route_service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.fixture
def rotate_access_token_api(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    current_user = User(
        id=uuid4(),
        access_token_digest="a" * 64,
        created_at=datetime(2026, 8, 10, 8, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 10, 8, 31, tzinfo=timezone.utc),
    )
    replacement_token = "B" * 43
    rotate = AsyncMock(return_value=replacement_token)
    service = Mock()
    service.rotate_access_token = rotate
    service_factory = Mock(return_value=service)
    monkeypatch.setattr(users_module, "UserService", service_factory)

    async def override_db_session():
        yield session

    async def override_current_user():
        return current_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield (
                client,
                session,
                current_user,
                replacement_token,
                service_factory,
                rotate,
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.parametrize("payload", [None, {}])
def test_rotate_access_token_accepts_omitted_or_empty_body_with_exact_response(
    rotate_access_token_api,
    payload,
):
    (
        client,
        session,
        current_user,
        replacement_token,
        service_factory,
        rotate,
    ) = rotate_access_token_api

    if payload is None:
        response = client.post("/api/v1/users/me/access-token/rotate")
    else:
        response = client.post(
            "/api/v1/users/me/access-token/rotate",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": replacement_token,
        "token_type": "bearer",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.text.count(replacement_token) == 1
    assert "user_id" not in response.text
    assert "owner_id" not in response.text
    assert "digest" not in response.text
    service_factory.assert_called_once_with(session)
    rotate.assert_awaited_once_with(
        current_user.id,
        current_user.access_token_digest,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": str(uuid4())},
        {"owner_id": str(uuid4())},
        {"token": "client-controlled"},
        {"access_token": "client-controlled"},
        {"digest": "client-controlled"},
        {"access_token_digest": "client-controlled"},
        {"unknown": "client-controlled"},
    ],
)
def test_rotate_access_token_rejects_all_fields_before_service_construction(
    rotate_access_token_api,
    payload,
):
    client, session, _user, _token, service_factory, rotate = (
        rotate_access_token_api
    )

    response = client.post(
        "/api/v1/users/me/access-token/rotate",
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    service_factory.assert_not_called()
    rotate.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_rotate_access_token_compare_and_swap_miss_returns_exact_conflict(
    rotate_access_token_api,
):
    client, session, current_user, replacement_token, service_factory, rotate = (
        rotate_access_token_api
    )
    rotate.return_value = None

    response = client.post("/api/v1/users/me/access-token/rotate")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Access token rotation conflict",
    }
    assert replacement_token not in response.text
    assert "access_token" not in response.text
    service_factory.assert_called_once_with(session)
    rotate.assert_awaited_once_with(
        current_user.id,
        current_user.access_token_digest,
    )


def test_rotate_access_token_without_authenticated_digest_conflicts_before_service(
    rotate_access_token_api,
):
    client, session, current_user, _token, service_factory, rotate = (
        rotate_access_token_api
    )
    current_user.access_token_digest = None

    response = client.post("/api/v1/users/me/access-token/rotate")

    assert response.status_code == 409
    assert response.json()["error"]["message"] == (
        "Access token rotation conflict"
    )
    service_factory.assert_not_called()
    rotate.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_rotate_access_token_failure_uses_safe_existing_error_contract(
    rotate_access_token_api,
):
    client, session, current_user, replacement_token, service_factory, rotate = (
        rotate_access_token_api
    )
    rotate.side_effect = RuntimeError("sensitive credential persistence detail")

    response = client.post("/api/v1/users/me/access-token/rotate")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert replacement_token not in response.text
    assert current_user.access_token_digest not in response.text
    assert "sensitive credential persistence detail" not in response.text
    service_factory.assert_called_once_with(session)
    rotate.assert_awaited_once()


def test_provisioning_authorization_hashes_then_uses_constant_time_comparison(
    monkeypatch,
):
    compare_digest = Mock(return_value=True)
    monkeypatch.setattr(
        security_module.secrets,
        "compare_digest",
        compare_digest,
    )

    authorized = security_module.is_user_provisioning_authorized(
        _PROVISIONING_TOKEN,
        _PROVISIONING_DIGEST,
    )

    assert authorized is True
    compare_digest.assert_called_once_with(
        security_module.digest_access_token(_PROVISIONING_TOKEN),
        _PROVISIONING_DIGEST,
    )


def test_create_user_accepts_strict_empty_object_with_existing_response(user_api):
    client, session, created_user, access_token, service_factory, provision = user_api

    response = client.post(
        "/api/v1/users",
        headers=_PROVISIONING_HEADERS,
        json={},
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": str(created_user.id),
        "created_at": "2026-08-10T08:30:00Z",
        "updated_at": "2026-08-10T08:31:00Z",
        "access_token": access_token,
        "token_type": "bearer",
    }
    assert response.headers["Cache-Control"] == "no-store"
    service_factory.assert_called_once_with(session)
    provision.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("configured_digest", "presented_token"),
    [
        (None, _PROVISIONING_TOKEN),
        (_PROVISIONING_DIGEST, None),
        (_PROVISIONING_DIGEST, "short"),
        (_PROVISIONING_DIGEST, "P" * 42 + "!"),
        (_PROVISIONING_DIGEST, "W" * 43),
    ],
)
def test_create_user_unauthorized_cases_share_exact_403_before_service_or_database(
    user_api,
    monkeypatch,
    caplog,
    configured_digest,
    presented_token,
):
    client, session, _user, _access_token, service_factory, provision = user_api
    monkeypatch.setattr(
        users_module.settings,
        "USER_PROVISIONING_TOKEN_DIGEST",
        configured_digest,
    )
    headers = {}
    if presented_token is not None:
        headers["X-User-Provisioning-Token"] = presented_token

    response = client.post("/api/v1/users", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "User provisioning is not authorized",
    }
    assert _PROVISIONING_TOKEN not in response.text
    assert _PROVISIONING_DIGEST not in response.text
    if presented_token is not None:
        assert presented_token not in response.text
        assert presented_token not in caplog.text
    assert _PROVISIONING_DIGEST not in caplog.text
    service_factory.assert_not_called()
    provision.assert_not_awaited()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()


def test_bearer_header_cannot_authorize_user_provisioning(user_api):
    client, session, _user, _access_token, service_factory, provision = user_api

    response = client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {_PROVISIONING_TOKEN}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "User provisioning is not authorized"
    )
    service_factory.assert_not_called()
    provision.assert_not_awaited()
    session.execute.assert_not_awaited()


def test_provisioning_header_cannot_authenticate_bearer_route(user_api):
    client, session, _user, _access_token, service_factory, provision = user_api

    response = client.get(
        "/api/v1/users/me",
        headers=_PROVISIONING_HEADERS,
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == (
        "Invalid authentication credentials"
    )
    assert response.headers["WWW-Authenticate"] == "Bearer"
    service_factory.assert_not_called()
    provision.assert_not_awaited()
    session.execute.assert_not_awaited()


def test_unauthorized_create_stops_before_database_dependency(monkeypatch):
    session = AsyncMock(spec=AsyncSession)
    database_dependency_started = Mock()
    service_factory = Mock()
    monkeypatch.setattr(users_module, "UserService", service_factory)
    monkeypatch.setattr(
        users_module.settings,
        "USER_PROVISIONING_TOKEN_DIGEST",
        _PROVISIONING_DIGEST,
    )

    async def override_db_session():
        database_dependency_started()
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/v1/users")
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 403
    assert response.json()["error"]["message"] == (
        "User provisioning is not authorized"
    )
    database_dependency_started.assert_not_called()
    service_factory.assert_not_called()
    session.execute.assert_not_awaited()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
