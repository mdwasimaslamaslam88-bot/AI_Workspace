from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.dependencies as authentication_module
import app.api.v1.ai as ai_module
from app.ai.catalog import (
    ModelAvailability,
    ModelCatalog,
    ModelCapability,
    ModelModality,
    ModelRuntimeUnavailableError,
    RuntimeModel,
)
from app.api.dependencies import get_current_user
from app.core.security import digest_access_token
from app.db.dependencies import get_db_session
from app.main import app
from app.models.user import User


def _current_user() -> User:
    return User(
        id=uuid4(),
        created_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def _runtime(models: tuple[RuntimeModel, ...]) -> Mock:
    runtime = Mock(runtime_id="local-runtime")
    runtime.discover_models = AsyncMock(return_value=models)
    return runtime


@pytest.fixture
def authenticated_ai_client():
    user = _current_user()

    async def override_current_user():
        return user

    app.dependency_overrides[get_current_user] = override_current_user
    previous_catalog = getattr(app.state, "model_catalog", None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, user
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.state.model_catalog = previous_catalog


def test_authenticated_model_listing_returns_exact_safe_normalized_metadata(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    raw_reference = "/private/runtime/models/secret:70b"
    raw_runtime_url = "http://127.0.0.1:11434"
    runtime = _runtime(
        (
            RuntimeModel(
                reference="zeta-tag",
                display_name="Zeta 7B",
            ),
            RuntimeModel(
                reference=raw_reference,
                display_name="Alpha 70B",
                family="AlphaFamily",
                parameter_class="70B",
                capabilities=("CHAT", "code", "text-generation"),
                context_window=131072,
                quantization="Q4_K_M",
                estimated_vram_bytes=48_000_000_000,
                availability=ModelAvailability.AVAILABLE,
            ),
        )
    )
    app.state.model_catalog = ModelCatalog((runtime,))

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    payload = response.json()
    assert [item["display_name"] for item in payload["items"]] == [
        "Alpha 70B",
        "Zeta 7B",
    ]
    first = payload["items"][0]
    assert set(first) == {
        "model_id",
        "display_name",
        "runtime_id",
        "modality",
        "family",
        "parameter_class",
        "capabilities",
        "context_window",
        "quantization",
        "estimated_vram_bytes",
        "availability",
    }
    assert first == {
        "model_id": first["model_id"],
        "display_name": "Alpha 70B",
        "runtime_id": "local-runtime",
        "modality": ModelModality.TEXT.value,
        "family": "AlphaFamily",
        "parameter_class": "70B",
        "capabilities": [
            ModelCapability.CHAT.value,
            ModelCapability.CODE.value,
            ModelCapability.TEXT_GENERATION.value,
        ],
        "context_window": 131072,
        "quantization": "Q4_K_M",
        "estimated_vram_bytes": 48_000_000_000,
        "availability": ModelAvailability.AVAILABLE.value,
    }
    assert first["model_id"].startswith("local-runtime:")
    assert raw_reference not in response.text
    assert raw_runtime_url not in response.text
    assert "/private/" not in response.text
    runtime.discover_models.assert_awaited_once_with()


def test_unconfigured_catalog_returns_empty_200(authenticated_ai_client):
    client, _user = authenticated_ai_client
    app.state.model_catalog = ModelCatalog()

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_authentication_completes_before_discovery_without_database_writes(
    monkeypatch,
):
    access_token = "A" * 43
    session = AsyncMock(spec=AsyncSession)
    user = _current_user()
    events = Mock()
    lookup = AsyncMock(return_value=user)
    catalog_list = AsyncMock(return_value=())
    events.attach_mock(lookup, "authenticate")
    events.attach_mock(catalog_list, "discover")
    user_service_factory = Mock(
        return_value=Mock(get_by_access_token_digest=lookup)
    )
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        user_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    previous_catalog = getattr(app.state, "model_catalog", None)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.model_catalog = Mock(list_models=catalog_list)
            response = client.get(
                "/api/v1/ai/models",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.state.model_catalog = previous_catalog

    assert response.status_code == 200
    user_service_factory.assert_called_once_with(session)
    assert events.method_calls == [
        call.authenticate(digest_access_token(access_token)),
        call.discover(),
    ]
    session.commit.assert_not_awaited()
    session.rollback.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer short", f"Bearer {'U' * 43}"],
)
def test_model_listing_preserves_uniform_401_before_discovery(
    monkeypatch,
    authorization,
):
    session = AsyncMock(spec=AsyncSession)
    lookup = AsyncMock(return_value=None)
    user_service_factory = Mock(
        return_value=Mock(get_by_access_token_digest=lookup)
    )
    catalog_list = AsyncMock(return_value=())
    monkeypatch.setattr(
        authentication_module,
        "UserService",
        user_service_factory,
    )

    async def override_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_db_session
    previous_catalog = getattr(app.state, "model_catalog", None)
    headers = {} if authorization is None else {"Authorization": authorization}
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.model_catalog = Mock(list_models=catalog_list)
            response = client.get("/api/v1/ai/models", headers=headers)
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.state.model_catalog = previous_catalog

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    catalog_list.assert_not_awaited()
    session.commit.assert_not_awaited()
    if authorization == f"Bearer {'U' * 43}":
        user_service_factory.assert_called_once_with(session)
        lookup.assert_awaited_once_with(digest_access_token("U" * 43))
    else:
        user_service_factory.assert_not_called()
        lookup.assert_not_awaited()


def test_configured_unavailable_runtime_returns_safe_generic_503(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    catalog_list = AsyncMock(
        side_effect=ModelRuntimeUnavailableError(
            "secret URL http://127.0.0.1:11434/private/path"
        )
    )
    app.state.model_catalog = Mock(list_models=catalog_list)

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Local model runtime unavailable",
    }
    assert "127.0.0.1" not in response.text
    assert "private/path" not in response.text


def test_unexpected_catalog_failure_uses_safe_generic_500(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    catalog_list = AsyncMock(
        side_effect=RuntimeError("secret internal runtime exception")
    )
    app.state.model_catalog = Mock(list_models=catalog_list)

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred.",
    }
    assert "secret internal runtime exception" not in response.text


def test_ai_route_scope_is_discovery_only_and_existing_routes_remain_registered(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    app.state.model_catalog = ModelCatalog()
    ai_methods = {
        method
        for route in ai_module.router.routes
        if getattr(route, "path", None) == "/ai/models"
        for method in getattr(route, "methods", ())
    }

    assert ai_methods == {"GET"}
    assert client.get("/api/v1/ai/models").status_code == 200
    assert client.get("/api/v1/users/me").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.post("/api/v1/ai/models").status_code == 405
    assert client.get("/api/v1/ai/generate").status_code == 404
    assert client.post("/api/v1/ai/pull").status_code == 404
    assert client.post("/api/v1/ai/load").status_code == 404
    assert client.get("/api/v1/messages").status_code == 404
