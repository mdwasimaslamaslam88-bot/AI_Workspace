import asyncio
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
    ModelDescriptor,
    ModelModality,
    ModelRuntimeUnavailableError,
    RuntimeModel,
)
from app.api.dependencies import get_current_user
from app.core.security import digest_access_token
from app.db.dependencies import get_db_session
from app.main import app
from app.models.user import User
from app.schemas.ai import LocalModelPageResponse, LocalModelResponse


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


def _descriptor(
    index: int = 1,
    *,
    display_name: str = "Model One",
    family: str | None = None,
    parameter_class: str | None = None,
    capabilities: tuple[ModelCapability, ...] = (),
    context_window: int | None = None,
    quantization: str | None = None,
    estimated_vram_bytes: int | None = None,
) -> ModelDescriptor:
    return ModelDescriptor(
        model_id=f"local-runtime:{index:024x}",
        display_name=display_name,
        runtime_id="local-runtime",
        modality=ModelModality.TEXT,
        family=family,
        parameter_class=parameter_class,
        capabilities=capabilities,
        context_window=context_window,
        quantization=quantization,
        estimated_vram_bytes=estimated_vram_bytes,
        availability=ModelAvailability.AVAILABLE,
    )


def _public_page(
    models: tuple[ModelDescriptor, ...],
) -> LocalModelPageResponse:
    return LocalModelPageResponse(
        items=[
            LocalModelResponse.model_validate(model, from_attributes=True)
            for model in models
        ]
    )


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
    page = LocalModelPageResponse.model_validate_json(response.content)
    assert ai_module._model_list_response_json_size(
        page,
        maximum=1_048_576,
    ) == len(response.content)
    assert response.headers["Content-Length"] == str(len(response.content))
    assert "Content-Encoding" not in response.headers
    assert "Transfer-Encoding" not in response.headers
    runtime.discover_models.assert_awaited_once_with()


def test_unconfigured_catalog_returns_empty_200(authenticated_ai_client):
    client, _user = authenticated_ai_client
    app.state.model_catalog = ModelCatalog()

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert response.content == b'{"items":[]}'
    assert response.headers["Content-Length"] == str(len(response.content))
    page = LocalModelPageResponse.model_validate_json(response.content)
    assert ai_module._model_list_response_json_size(
        page,
        maximum=len(response.content),
    ) == len(response.content)


def test_model_listing_exact_response_cap_preserves_framework_bytes(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    models = (
        _descriptor(
            display_name="Alpha 70B",
            family="AlphaFamily",
            parameter_class="70B",
            capabilities=(
                ModelCapability.CHAT,
                ModelCapability.CODE,
                ModelCapability.TEXT_GENERATION,
            ),
            context_window=131_072,
            quantization="Q4_K_M",
            estimated_vram_bytes=48_000_000_000,
        ),
        _descriptor(index=2, display_name="Zeta 7B"),
    )
    page = _public_page(models)
    expected_body = page.__pydantic_serializer__.to_json(page)
    predicted_size = ai_module._model_list_response_json_size(
        page,
        maximum=len(expected_body),
    )
    app.state.model_catalog = Mock(
        list_models=AsyncMock(return_value=models)
    )
    app.state.model_list_max_response_bytes = len(expected_body)

    response = client.get("/api/v1/ai/models")

    assert predicted_size == len(expected_body)
    assert response.status_code == 200
    assert response.content == expected_body
    assert response.headers["Content-Length"] == str(len(expected_body))


def test_model_listing_cap_plus_one_returns_redacted_generic_503(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    private_marker = "Private Model Marker"
    models = (
        _descriptor(
            display_name=private_marker,
            family="PrivateFamily",
            capabilities=(ModelCapability.TEXT_GENERATION,),
        ),
    )
    page = _public_page(models)
    successful_size = len(page.__pydantic_serializer__.to_json(page))
    app.state.model_catalog = Mock(
        list_models=AsyncMock(return_value=models)
    )
    app.state.model_list_max_response_bytes = successful_size - 1

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Local model runtime unavailable",
    }
    assert private_marker not in response.text
    assert "PrivateFamily" not in response.text
    assert str(successful_size) not in response.text
    assert str(successful_size - 1) not in response.text
    assert models[0].model_id not in response.text


@pytest.mark.parametrize(
    "value",
    [0, 1, 9, 10, 99, 100, 999_999, 10**100, -(10**100)],
)
def test_model_list_integer_accounting_is_exact(value):
    expected_size = len(str(value))

    assert ai_module._integer_json_size(value, expected_size) == expected_size
    with pytest.raises(ai_module._ModelListResponseTooLarge):
        ai_module._integer_json_size(value, expected_size - 1)


def test_large_model_list_integer_rejects_without_decimal_conversion(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client

    class DecimalConversionGuard(int):
        def __str__(self) -> str:
            raise AssertionError("oversized integer was converted to decimal")

    huge_value = DecimalConversionGuard(1 << 3_500_000)
    with pytest.raises(ai_module._ModelListResponseTooLarge):
        ai_module._integer_json_size(huge_value, 1_048_576)

    model = _descriptor(estimated_vram_bytes=huge_value)
    app.state.model_catalog = Mock(
        list_models=AsyncMock(return_value=(model,))
    )

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Local model runtime unavailable",
    }


def test_default_model_list_response_budget_preserves_256_models(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    capabilities = tuple(ModelCapability)
    models = tuple(
        _descriptor(
            index=index + 1,
            display_name=f"Model {index:03d}",
            family="F" * 255,
            parameter_class="P" * 255,
            capabilities=capabilities,
            context_window=index + 1,
            quantization="Q" * 255,
            estimated_vram_bytes=index + 1,
        )
        for index in range(256)
    )
    catalog_list = AsyncMock(return_value=models)
    app.state.model_catalog = Mock(list_models=catalog_list)

    response = client.get("/api/v1/ai/models")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 256
    assert [item["model_id"] for item in payload["items"]] == [
        model.model_id for model in models
    ]
    assert len(response.content) < 1_048_576
    assert response.headers["Content-Length"] == str(len(response.content))
    catalog_list.assert_awaited_once_with()


def test_overflow_does_not_retain_or_poison_later_model_listing(
    authenticated_ai_client,
):
    client, _user = authenticated_ai_client
    runtime = _runtime(
        (
            RuntimeModel(
                reference="selected:latest",
                display_name="Selected Model",
                capabilities=(ModelCapability.TEXT_GENERATION,),
            ),
        )
    )
    catalog = ModelCatalog((runtime,))
    app.state.model_catalog = catalog
    app.state.model_list_max_response_bytes = 1

    overflow = client.get("/api/v1/ai/models")
    assert overflow.status_code == 503
    assert catalog._list_models_flight is None

    app.state.model_list_max_response_bytes = 1_048_576
    retry = client.get("/api/v1/ai/models")

    assert retry.status_code == 200
    assert [item["display_name"] for item in retry.json()["items"]] == [
        "Selected Model"
    ]
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_overlapping_model_list_requests_share_discovery_but_account_separately(
    monkeypatch,
):
    discovery_started = asyncio.Event()
    release_discovery = asyncio.Event()
    both_callers_joined = asyncio.Event()
    model = RuntimeModel(
        reference="shared:latest",
        display_name="Shared Model",
        capabilities=(ModelCapability.TEXT_GENERATION,),
    )

    async def discover_models(*, reference_selector=None):
        assert reference_selector is None
        discovery_started.set()
        await release_discovery.wait()
        return (model,)

    runtime = Mock(runtime_id="local-runtime")
    runtime.discover_models = AsyncMock(side_effect=discover_models)
    catalog = ModelCatalog((runtime,))
    original_join = catalog._join_list_models_flight
    joined_callers = 0

    async def observed_join():
        nonlocal joined_callers
        flight = await original_join()
        joined_callers += 1
        if joined_callers == 2:
            both_callers_joined.set()
        return flight

    monkeypatch.setattr(catalog, "_join_list_models_flight", observed_join)
    response_accounting = Mock(
        wraps=ai_module._model_list_response_json_size
    )
    monkeypatch.setattr(
        ai_module,
        "_model_list_response_json_size",
        response_accounting,
    )
    request = Mock()
    request.app.state.model_catalog = catalog
    request.app.state.model_list_max_response_bytes = 1_048_576

    caller_a = asyncio.create_task(
        ai_module.list_local_models(request, object())
    )
    await discovery_started.wait()
    caller_b = asyncio.create_task(
        ai_module.list_local_models(request, object())
    )
    await both_callers_joined.wait()
    release_discovery.set()
    response_a, response_b = await asyncio.gather(caller_a, caller_b)

    assert response_a == response_b
    assert runtime.discover_models.await_count == 1
    assert response_accounting.call_count == 2
    assert catalog._list_models_flight is None


@pytest.mark.asyncio
async def test_cancellation_after_discovery_propagates_before_serialization(
    monkeypatch,
):
    model = _descriptor(
        capabilities=(ModelCapability.TEXT_GENERATION,),
    )
    runtime = Mock(runtime_id="local-runtime")
    runtime.discover_models = AsyncMock(
        return_value=(
            RuntimeModel(
                reference="model-one",
                display_name=model.display_name,
                capabilities=model.capabilities,
            ),
        )
    )
    catalog = ModelCatalog((runtime,))
    request = Mock()
    request.app.state.model_catalog = catalog
    request.app.state.model_list_max_response_bytes = 1_048_576
    original_accounting = ai_module._model_list_response_json_size
    accounting_calls = 0

    def cancel_first(response, *, maximum):
        nonlocal accounting_calls
        accounting_calls += 1
        if accounting_calls == 1:
            raise asyncio.CancelledError
        return original_accounting(response, maximum=maximum)

    monkeypatch.setattr(
        ai_module,
        "_model_list_response_json_size",
        cancel_first,
    )

    with pytest.raises(asyncio.CancelledError):
        await ai_module.list_local_models(request, object())

    assert catalog._list_models_flight is None
    retry = await ai_module.list_local_models(request, object())
    assert retry.items[0].display_name == "Model One"
    assert runtime.discover_models.await_count == 2
    assert catalog._list_models_flight is None


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
    response_accounting = Mock(
        wraps=ai_module._model_list_response_json_size
    )
    monkeypatch.setattr(
        ai_module,
        "_model_list_response_json_size",
        response_accounting,
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
    response_accounting.assert_not_called()
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
