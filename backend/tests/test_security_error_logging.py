from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
from typing import Annotated
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

import app.exceptions.handlers as handlers_module
from app.api.dependencies import get_current_user
from app.db.dependencies import get_db_session
from app.exceptions.handlers import register_exception_handlers
from app.main import app as production_app
from app.middleware.application_error_boundary import (
    ApplicationErrorBoundaryMiddleware,
)
from app.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.models.user import User


_ALLOWED_ORIGIN = "https://allowed.example"
_VALID_MODEL_ID = f"ollama-local:{'a' * 24}"
_UNEXPECTED_EVENT = "unexpected_application_error"
_SAFE_VALIDATION_DETAIL = {
    "type": "request_validation",
    "loc": ["request"],
    "msg": "Request validation failed.",
}


class _ValidationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(pattern=r"^allowed$")
    model_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}:[a-f0-9]{24}$"
    )


def _build_test_app() -> FastAPI:
    api = FastAPI()
    register_exception_handlers(api)
    api.add_middleware(ApplicationErrorBoundaryMiddleware)
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[_ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api.add_middleware(RequestIDMiddleware)
    return api


def _assert_generic_error_response(response, *, status_code: int, message: str):
    payload = response.json()
    assert set(payload) == {"success", "error", "path", "timestamp"}
    assert payload["success"] is False
    assert payload["error"] == {
        "code": "INTERNAL_SERVER_ERROR" if status_code == 500 else "HTTP_ERROR",
        "message": message,
    }
    assert datetime.fromisoformat(payload["timestamp"])


def _assert_sanitized_validation_response(response, *, path: str):
    assert response.status_code == 422
    payload = response.json()
    assert set(payload) == {"success", "error", "path", "timestamp"}
    assert payload["success"] is False
    assert payload["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "Validation failed.",
        "details": [_SAFE_VALIDATION_DETAIL],
    }
    assert payload["path"] == path
    assert datetime.fromisoformat(payload["timestamp"])
    return payload


def _assert_single_request_id(response) -> str:
    request_ids = response.headers.get_list("x-request-id")
    assert len(request_ids) == 1
    return request_ids[0]


def _http_scope(path: str = "/cancelled") -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": (),
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def test_unexpected_exception_is_contained_logged_once_and_fully_redacted(
    caplog,
):
    caplog.set_level(logging.ERROR)
    api = _build_test_app()
    conversation_id = str(uuid4())
    user_id = str(uuid4())
    request_id = "PRIVATE-REQUEST-ID-MARKER"
    query_marker = "PRIVATE-QUERY-MARKER"
    body_marker = "PRIVATE-BODY-MARKER"
    access_token = "ACCESS-TOKEN-SECRET-MARKER"
    provisioning_token = "PROVISIONING-TOKEN-SECRET-MARKER"
    password = "PASSWORD-SECRET-MARKER"
    sql = "SELECT PRIVATE-SQL-MARKER FROM credentials"
    sql_parameters = "PRIVATE-SQL-PARAMETERS-MARKER"
    model_reference = "/private/models/PRIVATE-MODEL-MARKER:latest"
    response_fragment = "PRIVATE-RESPONSE-FRAGMENT-MARKER"
    cause_marker = "PRIVATE-CAUSE-MARKER"
    context_marker = "PRIVATE-CONTEXT-MARKER"

    @api.post("/explode/{conversation_id}")
    async def explode(conversation_id: str, request: Request):
        assert await request.body() == body_marker.encode()
        try:
            raise RuntimeError(context_marker)
        except RuntimeError:
            try:
                raise ValueError(cause_marker)
            except ValueError as cause:
                raise RuntimeError(
                    " ".join(
                        (
                            access_token,
                            provisioning_token,
                            password,
                            sql,
                            sql_parameters,
                            model_reference,
                            conversation_id,
                            user_id,
                            response_fragment,
                        )
                    )
                ) from cause

    path = f"/explode/{conversation_id}"
    with TestClient(api) as client:
        response = client.post(
            f"{path}?private={query_marker}",
            content=body_marker,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-User-Provisioning-Token": provisioning_token,
                "X-Request-ID": request_id,
                "Origin": _ALLOWED_ORIGIN,
            },
        )

    assert response.status_code == 500
    _assert_generic_error_response(
        response,
        status_code=500,
        message="An unexpected error occurred.",
    )
    assert response.json()["path"] == path
    assert _assert_single_request_id(response) == request_id
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN

    safe_records = [
        record
        for record in caplog.records
        if _UNEXPECTED_EVENT in record.getMessage()
    ]
    assert len(safe_records) == 1
    assert safe_records[0].exc_info is None
    assert safe_records[0].stack_info is None

    logged = caplog.text
    assert _UNEXPECTED_EVENT in logged
    assert "method=" in logged and "POST" in logged
    assert "status_code=" in logged and "500" in logged
    assert "Traceback" not in logged
    assert "Exception in ASGI application" not in logged
    assert path not in logged
    for private_value in (
        conversation_id,
        user_id,
        request_id,
        query_marker,
        body_marker,
        access_token,
        provisioning_token,
        password,
        sql,
        sql_parameters,
        model_reference,
        response_fragment,
        cause_marker,
        context_marker,
    ):
        assert private_value not in logged


def test_unexpected_log_call_contains_only_fixed_safe_metadata(monkeypatch):
    safe_logger = Mock()
    monkeypatch.setattr(handlers_module, "logger", safe_logger)
    api = _build_test_app()

    @api.get("/explode")
    async def explode():
        raise RuntimeError("PRIVATE-EXCEPTION-MARKER")

    with TestClient(api) as client:
        response = client.get("/explode?private=PRIVATE-QUERY-MARKER")

    assert response.status_code == 500
    safe_logger.error.assert_called_once_with(
        _UNEXPECTED_EVENT,
        method="GET",
        status_code=500,
    )
    safe_logger.exception.assert_not_called()


@pytest.mark.parametrize(
    "case",
    ["invalid_prompt", "extra_field", "invalid_model", "malformed_json"],
)
def test_validation_logs_only_fixed_metadata_without_raw_input(caplog, case):
    caplog.set_level(logging.WARNING)
    api = _build_test_app()
    marker = f"PRIVATE-VALIDATION-{case}-MARKER"

    @api.post("/validate")
    async def validate(payload: _ValidationPayload):
        return payload

    with TestClient(api) as client:
        if case == "invalid_prompt":
            response = client.post(
                "/validate",
                json={"prompt": marker, "model_id": _VALID_MODEL_ID},
            )
        elif case == "extra_field":
            response = client.post(
                "/validate",
                json={
                    "prompt": "allowed",
                    "model_id": _VALID_MODEL_ID,
                    "private_extra": marker,
                },
            )
        elif case == "invalid_model":
            response = client.post(
                "/validate",
                json={"prompt": "allowed", "model_id": marker},
            )
        else:
            response = client.post(
                "/validate",
                content=f'{{"prompt":"{marker}"'.encode(),
                headers={"Content-Type": "application/json"},
            )

    _assert_sanitized_validation_response(response, path="/validate")
    response_text = response.text
    for private_value in (
        marker,
        "prompt",
        "model_id",
        "private_extra",
        "json_invalid",
        "JSON decode error",
    ):
        assert private_value not in response_text

    validation_records = [
        record
        for record in caplog.records
        if "validation_error" in record.getMessage()
    ]
    assert len(validation_records) == 1
    assert validation_records[0].exc_info is None
    logged = caplog.text
    assert marker not in logged
    assert "/validate" not in logged
    assert "errors=" not in logged
    assert "input=" not in logged
    assert "status_code=" in logged and "422" in logged
    assert "error_count=1" in logged
    assert _UNEXPECTED_EVENT not in logged


def test_query_validation_uses_only_fixed_safe_detail():
    api = _build_test_app()
    query_name = "private_query_parameter"
    query_value = "PRIVATE-QUERY-VALIDATION-VALUE"

    @api.get("/validate-query")
    async def validate_query(
        value: Annotated[int, Query(alias=query_name)],
    ):
        return {"value": value}

    with TestClient(api) as client:
        response = client.get(
            "/validate-query",
            params={query_name: query_value},
        )

    _assert_sanitized_validation_response(response, path="/validate-query")
    assert query_name not in response.text
    assert query_value not in response.text


def test_path_validation_uses_only_fixed_safe_detail():
    api = _build_test_app()
    path_value = "PRIVATE-CONVERSATION-ID"

    @api.get("/validate-path/{conversation_id}")
    async def validate_path(conversation_id: UUID):
        return {"conversation_id": conversation_id}

    path = f"/validate-path/{path_value}"
    with TestClient(api) as client:
        response = client.get(path)

    payload = _assert_sanitized_validation_response(response, path=path)
    serialized_details = json.dumps(payload["error"]["details"])
    assert "conversation_id" not in serialized_details
    assert path_value not in serialized_details


def test_header_validation_uses_only_fixed_safe_detail():
    api = _build_test_app()
    header_name = "X-Private-Validation"
    header_value = "PRIVATE-HEADER-VALIDATION-VALUE"

    @api.get("/validate-header")
    async def validate_header(
        value: Annotated[int, Header(alias=header_name)],
    ):
        return {"value": value}

    with TestClient(api) as client:
        response = client.get(
            "/validate-header",
            headers={header_name: header_value},
        )

    _assert_sanitized_validation_response(response, path="/validate-header")
    assert header_name not in response.text
    assert header_name.lower() not in response.text
    assert header_value not in response.text


def test_many_validation_failures_have_constant_public_detail_and_accurate_count(
    caplog,
):
    caplog.set_level(logging.WARNING)
    api = _build_test_app()
    failure_count = 2_000
    many_invalid = {
        f"private_extra_{index}": f"PRIVATE-MANY-{index}"
        for index in range(failure_count)
    }
    encoded_many = json.dumps(
        {
            "prompt": "allowed",
            "model_id": _VALID_MODEL_ID,
            **many_invalid,
        },
        separators=(",", ":"),
    ).encode()
    assert len(encoded_many) < 262_144

    @api.post("/validate-many")
    async def validate_many(payload: _ValidationPayload):
        return payload

    with TestClient(api) as client:
        one_error = client.post(
            "/validate-many",
            json={
                "prompt": "PRIVATE-SINGLE-ERROR",
                "model_id": _VALID_MODEL_ID,
            },
        )
        many_errors = client.post(
            "/validate-many",
            content=encoded_many,
            headers={"Content-Type": "application/json"},
        )

    one_payload = _assert_sanitized_validation_response(
        one_error,
        path="/validate-many",
    )
    many_payload = _assert_sanitized_validation_response(
        many_errors,
        path="/validate-many",
    )
    assert many_payload["error"] == one_payload["error"]
    assert len(many_errors.content) == len(one_error.content)
    assert "private_extra_" not in many_errors.text
    assert "PRIVATE-MANY-" not in many_errors.text

    validation_records = [
        record
        for record in caplog.records
        if "validation_error" in record.getMessage()
    ]
    assert len(validation_records) == 2
    assert all(record.exc_info is None for record in validation_records)
    logged = caplog.text
    assert "error_count=1" in logged
    assert f"error_count={failure_count}" in logged
    assert "private_extra_" not in logged
    assert "PRIVATE-MANY-" not in logged
    assert "errors=" not in logged
    assert "input=" not in logged


def test_validation_preserves_request_id_and_cors_headers():
    api = _build_test_app()
    request_id = "validation-request-id"

    @api.post("/validate-headers")
    async def validate_headers(payload: _ValidationPayload):
        return payload

    with TestClient(api) as client:
        response = client.post(
            "/validate-headers",
            json={
                "prompt": "PRIVATE-CORS-VALIDATION-VALUE",
                "model_id": _VALID_MODEL_ID,
            },
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "X-Request-ID": request_id,
            },
        )

    _assert_sanitized_validation_response(
        response,
        path="/validate-headers",
    )
    assert "PRIVATE-CORS-VALIDATION-VALUE" not in response.text
    assert _assert_single_request_id(response) == request_id
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN


@pytest.mark.parametrize(
    ("origin", "expected_status", "expected_body", "origin_is_allowed"),
    [
        (_ALLOWED_ORIGIN, 200, "OK", True),
        (
            "https://rejected.example",
            400,
            "Disallowed CORS origin",
            False,
        ),
    ],
)
@pytest.mark.parametrize("supplied_request_id", [None, "client-preflight-id"])
def test_cors_preflight_has_request_id_without_entering_application(
    origin,
    expected_status,
    expected_body,
    origin_is_allowed,
    supplied_request_id,
):
    api = _build_test_app()
    application_calls: list[str] = []

    async def forbidden_dependency():
        application_calls.append("dependency")
        raise AssertionError("preflight must not enter dependencies")

    @api.get(
        "/preflight",
        dependencies=[Depends(forbidden_dependency)],
    )
    async def preflight_target():
        application_calls.append("endpoint")
        raise AssertionError("preflight must not enter the endpoint")

    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-Custom-Header",
    }
    if supplied_request_id is not None:
        headers["X-Request-ID"] = supplied_request_id

    with TestClient(api) as client:
        response = client.options("/preflight", headers=headers)

    assert response.status_code == expected_status
    assert response.text == expected_body
    response_request_id = _assert_single_request_id(response)
    if supplied_request_id is None:
        assert str(UUID(response_request_id)) == response_request_id
    else:
        assert response_request_id == supplied_request_id
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-headers"] == "X-Custom-Header"
    if origin_is_allowed:
        assert response.headers["access-control-allow-origin"] == origin
    else:
        assert "access-control-allow-origin" not in response.headers
    assert application_calls == []


@pytest.mark.parametrize(
    ("supplied_request_id", "expected_request_id"),
    [
        (None, None),
        ("client-normal-id", "client-normal-id"),
        ("", None),
    ],
)
def test_normal_non_cors_request_id_semantics_are_unchanged(
    supplied_request_id,
    expected_request_id,
):
    api = _build_test_app()

    @api.get("/request-id")
    async def request_id_target():
        return {"ok": True}

    headers = {}
    if supplied_request_id is not None:
        headers["X-Request-ID"] = supplied_request_id

    with TestClient(api) as client:
        response = client.get("/request-id", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    response_request_id = _assert_single_request_id(response)
    if expected_request_id is None:
        assert str(UUID(response_request_id)) == response_request_id
    else:
        assert response_request_id == expected_request_id
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    ("origin", "expected_allow_origin"),
    [
        (_ALLOWED_ORIGIN, _ALLOWED_ORIGIN),
        ("https://rejected.example", None),
    ],
)
def test_normal_cors_response_and_request_id_semantics_are_unchanged(
    origin,
    expected_allow_origin,
):
    api = _build_test_app()
    request_id = "normal-cors-request-id"

    @api.get("/normal-cors")
    async def normal_cors():
        return {"ok": True}

    with TestClient(api) as client:
        response = client.get(
            "/normal-cors",
            headers={"Origin": origin, "X-Request-ID": request_id},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _assert_single_request_id(response) == request_id
    if expected_allow_origin is None:
        assert "access-control-allow-origin" not in response.headers
    else:
        assert response.headers["access-control-allow-origin"] == (
            expected_allow_origin
        )


def test_duplicate_incoming_request_id_preserves_first_value_behavior():
    api = _build_test_app()

    @api.get("/duplicate-request-id")
    async def duplicate_request_id(request: Request):
        return {"request_id": request.state.request_id}

    with TestClient(api) as client:
        response = client.get(
            "/duplicate-request-id",
            headers=[
                ("X-Request-ID", "first-request-id"),
                ("X-Request-ID", "second-request-id"),
            ],
        )

    assert response.status_code == 200
    assert response.json() == {"request_id": "first-request-id"}
    assert _assert_single_request_id(response) == "first-request-id"


@pytest.mark.parametrize(
    ("status_code", "message", "headers"),
    [
        (401, "Invalid authentication credentials", {"WWW-Authenticate": "Bearer"}),
        (403, "User provisioning is not authorized", None),
        (404, "Conversation not found", None),
        (409, "Conversation changed during generation", None),
        (503, "Local model runtime unavailable", None),
    ],
)
def test_handled_http_exceptions_preserve_contract_without_unexpected_log(
    caplog,
    status_code,
    message,
    headers,
):
    caplog.set_level(logging.ERROR)
    api = _build_test_app()
    request_id = f"handled-{status_code}-request-id"

    @api.get("/handled")
    async def handled():
        raise HTTPException(
            status_code=status_code,
            detail=message,
            headers=headers,
        )

    with TestClient(api) as client:
        response = client.get(
            "/handled",
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == status_code
    _assert_generic_error_response(
        response,
        status_code=status_code,
        message=message,
    )
    assert response.json()["path"] == "/handled"
    if headers is not None:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert _assert_single_request_id(response) == request_id
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN
    assert _UNEXPECTED_EVENT not in caplog.text


def test_invalid_bearer_preserves_uniform_401_without_unexpected_log(caplog):
    caplog.set_level(logging.ERROR)
    api = _build_test_app()
    session = AsyncMock(spec=AsyncSession)

    async def override_db_session():
        yield session

    @api.get("/protected")
    async def protected(
        _current_user: Annotated[User, Depends(get_current_user)],
    ):
        return {"ok": True}

    api.dependency_overrides[get_db_session] = override_db_session
    request_id = "invalid-bearer-request-id"
    with TestClient(api) as client:
        response = client.get(
            "/protected",
            headers={
                "Authorization": "Bearer short",
                "Origin": _ALLOWED_ORIGIN,
                "X-Request-ID": request_id,
            },
        )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Invalid authentication credentials",
    }
    assert _assert_single_request_id(response) == request_id
    assert response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN
    assert _UNEXPECTED_EVENT not in caplog.text
    session.execute.assert_not_awaited()
    session.commit.assert_not_awaited()


def test_production_middleware_order_is_request_id_then_cors_then_boundaries():
    middleware_types = [entry.cls for entry in production_app.user_middleware]

    assert middleware_types == [
        RequestIDMiddleware,
        CORSMiddleware,
        RequestBodyLimitMiddleware,
        ApplicationErrorBoundaryMiddleware,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_length", "expected_status", "expected_message"),
    [
        (b"invalid", 400, "Invalid request headers"),
        (b"5", 413, "Request body is too large"),
    ],
)
async def test_body_limit_errors_keep_cors_and_one_request_id(
    content_length,
    expected_status,
    expected_message,
):
    application_called = False
    sent: list[Message] = []
    incoming = [
        {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }
    ]

    async def forbidden_application(
        _scope: Scope,
        _receive: Receive,
        _send: Send,
    ) -> None:
        nonlocal application_called
        application_called = True
        raise AssertionError("rejected request must not enter the application")

    async def receive() -> Message:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    inner = ApplicationErrorBoundaryMiddleware(forbidden_application)
    limited = RequestBodyLimitMiddleware(inner, max_body_bytes=4)
    cors = CORSMiddleware(
        limited,
        allow_origins=[_ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    middleware = RequestIDMiddleware(cors)
    scope = _http_scope("/limited")
    scope["method"] = "POST"
    scope["headers"] = [
        (b"origin", _ALLOWED_ORIGIN.encode()),
        (b"x-request-id", b"body-limit-request-id"),
        (b"content-length", content_length),
    ]

    await middleware(scope, receive, send)

    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    payload = json.loads(body)
    response_headers = start["headers"]
    request_ids = [
        value.decode()
        for name, value in response_headers
        if name.lower() == b"x-request-id"
    ]
    allow_origins = [
        value.decode()
        for name, value in response_headers
        if name.lower() == b"access-control-allow-origin"
    ]

    assert start["status"] == expected_status
    assert payload["error"] == {
        "code": "HTTP_ERROR",
        "message": expected_message,
    }
    assert payload["path"] == "/limited"
    assert request_ids == ["body-limit-request-id"]
    assert allow_origins == [_ALLOWED_ORIGIN]
    assert application_called is False


@pytest.mark.asyncio
async def test_full_middleware_stack_propagates_cancellation_without_response():
    entered = asyncio.Event()
    never = asyncio.Event()
    sent: list[Message] = []

    async def inner(_scope: Scope, _receive: Receive, _send: Send) -> None:
        entered.set()
        await never.wait()

    async def receive() -> Message:
        await never.wait()
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        sent.append(message)

    boundary = ApplicationErrorBoundaryMiddleware(inner)
    limited = RequestBodyLimitMiddleware(boundary, max_body_bytes=4)
    cors = CORSMiddleware(
        limited,
        allow_origins=[_ALLOWED_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    middleware = RequestIDMiddleware(cors)
    task = asyncio.create_task(
        middleware(
            _http_scope("/cancelled-full-stack"),
            receive,
            send,
        )
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent == []


@pytest.mark.asyncio
async def test_direct_task_cancellation_propagates_without_response_or_log(caplog):
    caplog.set_level(logging.ERROR)
    entered = asyncio.Event()
    never = asyncio.Event()
    sent: list[Message] = []

    async def inner(_scope: Scope, _receive: Receive, _send: Send) -> None:
        entered.set()
        await never.wait()

    async def receive() -> Message:
        await never.wait()
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        sent.append(message)

    task = asyncio.create_task(
        ApplicationErrorBoundaryMiddleware(inner)(
            _http_scope(),
            receive,
            send,
        )
    )
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent == []
    assert _UNEXPECTED_EVENT not in caplog.text


@pytest.mark.asyncio
async def test_disconnect_cancellation_propagates_without_generic_logging(caplog):
    caplog.set_level(logging.ERROR)
    messages = iter(({"type": "http.disconnect"},))
    sent: list[Message] = []

    async def inner(_scope: Scope, receive: Receive, _send: Send) -> None:
        message = await receive()
        assert message["type"] == "http.disconnect"
        raise asyncio.CancelledError

    async def receive() -> Message:
        return next(messages)

    async def send(message: Message) -> None:
        sent.append(message)

    with pytest.raises(asyncio.CancelledError):
        await ApplicationErrorBoundaryMiddleware(inner)(
            _http_scope("/private-disconnect-id"),
            receive,
            send,
        )

    assert sent == []
    assert _UNEXPECTED_EVENT not in caplog.text
    assert "private-disconnect-id" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["dependency", "service"])
async def test_dependency_and_service_cancellation_propagate_unchanged(
    caplog,
    stage,
):
    caplog.set_level(logging.ERROR)
    entered = asyncio.Event()
    never = asyncio.Event()
    sent: list[Message] = []

    async def blocked_operation() -> None:
        entered.set()
        await never.wait()

    async def service_layer(
        _scope: Scope,
        _receive: Receive,
        _send: Send,
    ) -> None:
        if stage == "service":
            await blocked_operation()

    async def dependency_layer(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if stage == "dependency":
            await blocked_operation()
        await service_layer(scope, receive, send)

    async def receive() -> Message:
        await never.wait()
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        sent.append(message)

    task = asyncio.create_task(
        ApplicationErrorBoundaryMiddleware(
            dependency_layer,
        )(
            _http_scope(f"/cancel-{stage}"),
            receive,
            send,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sent == []
    assert _UNEXPECTED_EVENT not in caplog.text
