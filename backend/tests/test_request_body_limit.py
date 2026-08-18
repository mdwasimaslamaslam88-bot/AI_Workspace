import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.dependencies as authentication_module
import app.api.v1.conversations as conversations_module
import app.api.v1.users as users_module
from app.api.dependencies import get_current_user
from app.core.config import MAX_REQUEST_BODY_BYTES, settings
from app.db.dependencies import get_db_session
from app.main import app
from app.middleware.request_body_limit import RequestBodyLimitMiddleware


def _http_scope(headers=(), path="/limited"):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }


def _request(body, *, more_body=False):
    return {
        "type": "http.request",
        "body": body,
        "more_body": more_body,
    }


async def _invoke(middleware, scope, incoming):
    messages = list(incoming)
    sent = []
    receive_calls = 0

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent, receive_calls


def _response(sent):
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start, json.loads(body)


def _assert_safe_error(sent, status_code, message):
    start, payload = _response(sent)
    assert start["status"] == status_code
    assert payload["success"] is False
    assert payload["error"] == {
        "code": "HTTP_ERROR",
        "message": message,
    }
    assert payload["path"] == "/limited"
    assert isinstance(payload["timestamp"], str)
    return payload


def _consuming_app(seen, *, status_code=204):
    async def downstream(_scope, receive, send):
        while True:
            message = await receive()
            seen.append(message)
            if message["type"] == "http.disconnect":
                break
            if not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    return downstream


@pytest.mark.parametrize(
    "max_body_bytes",
    [None, True, False, "1", 1.0, 0, -1, MAX_REQUEST_BODY_BYTES + 1],
)
def test_middleware_rejects_invalid_limits(max_body_bytes):
    with pytest.raises((TypeError, ValueError)):
        RequestBodyLimitMiddleware(Mock(), max_body_bytes)


@pytest.mark.asyncio
async def test_declared_oversize_is_immediate_without_receive_or_downstream():
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(downstream, 4)
    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", b"999999"),)),
        (_request(b"secret"),),
    )

    _assert_safe_error(sent, 413, "Request body is too large")
    assert receive_calls == 0
    assert downstream_called is False
    response_text = json.dumps(_response(sent)[1])
    assert "secret" not in response_text
    assert "999999" not in response_text


@pytest.mark.asyncio
async def test_exactly_at_cap_reaches_downstream_unchanged():
    seen = []
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 4)
    incoming = (_request(b"ab", more_body=True), _request(b"cd"))

    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", b"4"),)),
        incoming,
    )

    assert receive_calls == 2
    assert seen == list(incoming)
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 204


@pytest.mark.asyncio
async def test_cap_plus_one_is_rejected_and_overflow_chunk_is_not_forwarded():
    seen = []
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 4)

    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(),
        (_request(b"abcd", more_body=True), _request(b"e")),
    )

    _assert_safe_error(sent, 413, "Request body is too large")
    assert receive_calls == 2
    assert seen == [_request(b"abcd", more_body=True)]


@pytest.mark.asyncio
async def test_absent_content_length_counts_cumulative_chunks():
    seen = []
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 5)
    incoming = (
        _request(b"a", more_body=True),
        _request(b"bc", more_body=True),
        _request(b"de"),
    )

    sent, _receive_calls = await _invoke(
        middleware,
        _http_scope(),
        incoming,
    )

    assert seen == list(incoming)
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 204


@pytest.mark.asyncio
async def test_understated_content_length_does_not_bypass_actual_byte_limit():
    seen = []
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 4)

    sent, _receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", b"2"),)),
        (_request(b"abc", more_body=True), _request(b"de")),
    )

    _assert_safe_error(sent, 413, "Request body is too large")
    assert seen == [_request(b"abc", more_body=True)]


@pytest.mark.parametrize(
    "value",
    [b"", b"invalid", b"+1", b"1.0", b"1, 1"],
)
@pytest.mark.asyncio
async def test_malformed_or_comma_ambiguous_content_length_is_safe_400(value):
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(downstream, 4)
    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", value),)),
        (_request(b"secret body"),),
    )

    payload = _assert_safe_error(sent, 400, "Invalid request headers")
    assert downstream_called is False
    assert receive_calls == 0
    response_text = json.dumps(payload)
    if value:
        assert value.decode(errors="ignore") not in response_text
    assert "secret body" not in response_text


@pytest.mark.asyncio
async def test_negative_content_length_is_safe_400():
    middleware = RequestBodyLimitMiddleware(Mock(), 4)

    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", b"-1"),)),
        (_request(b"secret"),),
    )

    _assert_safe_error(sent, 400, "Invalid request headers")
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_duplicate_identical_content_length_is_unambiguous_and_accepted():
    seen = []
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 4)
    incoming = (_request(b"data"),)

    sent, _receive_calls = await _invoke(
        middleware,
        _http_scope(
            (
                (b"content-length", b"4"),
                (b"Content-Length", b"04"),
            )
        ),
        incoming,
    )

    assert seen == list(incoming)
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 204


@pytest.mark.asyncio
async def test_duplicate_conflicting_content_length_is_safe_400():
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(downstream, 4)
    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(
            (
                (b"content-length", b"3"),
                (b"content-length", b"4"),
            )
        ),
        (_request(b"data"),),
    )

    _assert_safe_error(sent, 400, "Invalid request headers")
    assert downstream_called is False
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_http_disconnect_is_forwarded_unchanged():
    seen = []
    disconnect = {"type": "http.disconnect"}
    middleware = RequestBodyLimitMiddleware(_consuming_app(seen), 4)

    sent, receive_calls = await _invoke(
        middleware,
        _http_scope(),
        (disconnect,),
    )

    assert receive_calls == 1
    assert seen == [disconnect]
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == 204


@pytest.mark.asyncio
async def test_non_http_scope_passes_through_unchanged():
    seen = []

    async def downstream(scope, receive, send):
        seen.append((scope, await receive()))
        await send({"type": "websocket.close", "code": 1000})

    middleware = RequestBodyLimitMiddleware(downstream, 1)
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "path": "/socket",
        "raw_path": b"/socket",
        "query_string": b"",
        "headers": [(b"content-length", b"999999")],
        "scheme": "ws",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "subprotocols": [],
    }
    incoming = {"type": "websocket.connect"}

    sent, receive_calls = await _invoke(middleware, scope, (incoming,))

    assert receive_calls == 1
    assert seen == [(scope, incoming)]
    assert sent == [{"type": "websocket.close", "code": 1000}]


@pytest.mark.parametrize(
    "status_code",
    [200, 201, 400, 401, 403, 404, 409, 422, 429, 503],
)
@pytest.mark.asyncio
async def test_within_limit_body_preserves_downstream_response(status_code):
    seen = []
    middleware = RequestBodyLimitMiddleware(
        _consuming_app(seen, status_code=status_code),
        4,
    )
    incoming = (_request(b"data"),)

    sent, _receive_calls = await _invoke(
        middleware,
        _http_scope(((b"content-length", b"4"),)),
        incoming,
    )

    assert seen == list(incoming)
    assert next(message for message in sent if message["type"] == "http.response.start")[
        "status"
    ] == status_code


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (
            "/api/v1/users",
            {"X-User-Provisioning-Token": "provisioning-secret"},
        ),
        (
            "/api/v1/conversations",
            {"Authorization": "Bearer authentication-secret"},
        ),
        (
            f"/api/v1/conversations/{uuid4()}/messages",
            {"Authorization": "Bearer authentication-secret"},
        ),
        (
            f"/api/v1/conversations/{uuid4()}/messages/generate",
            {"Authorization": "Bearer authentication-secret"},
        ),
    ],
)
def test_application_rejects_oversize_before_dependencies_and_services(
    monkeypatch,
    caplog,
    path,
    headers,
):
    forbidden_calls = []

    async def forbidden_dependency():
        forbidden_calls.append("dependency")
        raise AssertionError("request dependency must not run")
        yield

    async def forbidden_current_user():
        forbidden_calls.append("current_user")
        raise AssertionError("authentication must not run")

    async def forbidden_provisioning():
        forbidden_calls.append("provisioning")
        raise AssertionError("provisioning authorization must not run")

    def forbidden_service(*_args, **_kwargs):
        forbidden_calls.append("service")
        raise AssertionError("service must not be constructed")

    app.dependency_overrides[get_db_session] = forbidden_dependency
    app.dependency_overrides[get_current_user] = forbidden_current_user
    app.dependency_overrides[
        users_module._require_user_provisioning_authorization
    ] = forbidden_provisioning
    monkeypatch.setattr(authentication_module, "UserService", forbidden_service)
    monkeypatch.setattr(users_module, "UserService", forbidden_service)
    monkeypatch.setattr(conversations_module, "ConversationService", forbidden_service)
    monkeypatch.setattr(conversations_module, "MessageService", forbidden_service)
    monkeypatch.setattr(
        conversations_module,
        "ConversationGenerationService",
        forbidden_service,
    )
    secret_body = b'{' + b'"private":"' + (
        b"body-fragment" * (settings.REQUEST_MAX_BODY_BYTES // 13 + 1)
    ) + b'"}'
    assert len(secret_body) > settings.REQUEST_MAX_BODY_BYTES
    request_headers = {
        **headers,
        "Content-Type": "application/json",
        "Origin": "http://localhost:3000",
        "X-Request-ID": "body-limit-request-id",
    }

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(
                path,
                headers=request_headers,
                content=secret_body,
            )
    finally:
        app.dependency_overrides.pop(
            users_module._require_user_provisioning_authorization,
            None,
        )
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "HTTP_ERROR",
        "message": "Request body is too large",
    }
    assert response.headers["X-Request-ID"] == "body-limit-request-id"
    assert response.headers["Access-Control-Allow-Origin"] == (
        "http://localhost:3000"
    )
    assert forbidden_calls == []
    combined_output = response.text + caplog.text
    for private_value in (
        "body-fragment",
        "provisioning-secret",
        "authentication-secret",
        str(settings.REQUEST_MAX_BODY_BYTES),
        str(len(secret_body)),
    ):
        assert private_value not in combined_output
