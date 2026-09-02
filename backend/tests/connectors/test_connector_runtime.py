"""Network policy and execution tests for the connector runtime."""

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from app.connectors.credentials import ConnectorCredentialBox
from app.connectors.runtime import (
    ConnectorRuntime,
    ConnectorRuntimeError,
    normalize_connector_origin,
)
from app.connectors.service import ConnectorPermissionError, ConnectorService
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecutionStatus,
    ConnectorHealthStatus,
    ConnectorKind,
)


def _connector(
    box: ConnectorCredentialBox,
    *,
    max_retries: int = 1,
    rate_limit: int = 30,
) -> Connector:
    now = datetime.now(timezone.utc)
    return Connector(
        id=uuid4(),
        owner_id=uuid4(),
        name="Bounded API",
        kind=ConnectorKind.REST,
        base_url="https://api.example.test",
        auth_kind=ConnectorAuthKind.BEARER,
        credential_ciphertext=box.encrypt("private-connector-token-123456"),
        scopes_json='["read","write"]',
        path_prefixes_json='["/v1/"]',
        health_path="/v1/health",
        enabled=True,
        timeout_seconds=2,
        max_retries=max_retries,
        rate_limit_requests_per_minute=rate_limit,
        health_status=ConnectorHealthStatus.UNKNOWN,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_runtime_sends_fixed_auth_and_bounded_json_without_redirects(tmp_path):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["idempotency"] = request.headers.get("idempotency-key")
        seen["body"] = await request.aread()
        return httpx.Response(200, json={"accepted": True})

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(
        box,
        ("https://api.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    try:
        result = await runtime.execute(
            _connector(box),
            action=ConnectorAction.EXECUTE,
            method="POST",
            path="/v1/actions",
            json_body={"task": "safe"},
            idempotency_key="action-0000000001",
        )
    finally:
        await runtime.close()

    assert result.payload == {"accepted": True}
    assert result.attempts == 1
    assert seen == {
        "authorization": "Bearer private-connector-token-123456",
        "idempotency": "action-0000000001",
        "body": b'{"task":"safe"}',
    }


@pytest.mark.asyncio
async def test_runtime_retries_only_safe_or_idempotent_actions(tmp_path):
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={"calls": calls})

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(
        box,
        ("https://api.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        result = await runtime.execute(
            _connector(box),
            action=ConnectorAction.EXECUTE,
            method="GET",
            path="/v1/status",
            json_body=None,
            idempotency_key=None,
        )
    finally:
        await runtime.close()

    assert result.attempts == 2
    assert result.payload == {"calls": 2}


@pytest.mark.asyncio
async def test_runtime_does_not_retry_unsafe_write_without_idempotency(tmp_path):
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"retry": True})

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(
        box,
        ("https://api.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ConnectorRuntimeError) as failure:
            await runtime.execute(
                _connector(box, max_retries=2),
                action=ConnectorAction.EXECUTE,
                method="POST",
                path="/v1/actions",
                json_body={"task": "unsafe-to-repeat"},
                idempotency_key=None,
            )
    finally:
        await runtime.close()

    assert calls == 1
    assert failure.value.attempts == 1
    assert failure.value.code == "connector_http_error"


@pytest.mark.asyncio
async def test_runtime_rate_limit_counts_every_outbound_retry(tmp_path):
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"retry": True})

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(
        box,
        ("https://api.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ConnectorRuntimeError) as failure:
            await runtime.execute(
                _connector(box, max_retries=2, rate_limit=1),
                action=ConnectorAction.EXECUTE,
                method="GET",
                path="/v1/status",
                json_body=None,
                idempotency_key=None,
            )
    finally:
        await runtime.close()

    assert calls == 1
    assert failure.value.status is ConnectorExecutionStatus.RATE_LIMITED
    assert failure.value.attempts == 1


@pytest.mark.asyncio
async def test_runtime_rejects_non_finite_json_and_corrupt_credentials(tmp_path):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"unsafe":NaN}',
            headers={"Content-Type": "application/json"},
        )

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(
        box,
        ("https://api.example.test",),
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ConnectorRuntimeError) as invalid_json:
            await runtime.execute(
                _connector(box),
                action=ConnectorAction.EXECUTE,
                method="GET",
                path="/v1/status",
                json_body=None,
                idempotency_key=None,
            )
        corrupted = _connector(box)
        corrupted.credential_ciphertext = "not-authenticated-ciphertext"
        with pytest.raises(ConnectorRuntimeError) as invalid_credential:
            await runtime.execute(
                corrupted,
                action=ConnectorAction.EXECUTE,
                method="GET",
                path="/v1/status",
                json_body=None,
                idempotency_key=None,
            )
    finally:
        await runtime.close()

    assert invalid_json.value.code == "connector_response_invalid"
    assert invalid_credential.value.code == "connector_unavailable"
    assert invalid_credential.value.attempts == 0


@pytest.mark.asyncio
async def test_origin_policy_requires_https_except_loopback_and_exact_allowlisting(tmp_path):
    assert normalize_connector_origin("http://127.0.0.1:9010/") == "http://127.0.0.1:9010"
    with pytest.raises(ValueError, match="HTTPS"):
        normalize_connector_origin("http://example.com")
    with pytest.raises(ValueError, match="credential-free"):
        normalize_connector_origin("https://user:secret@example.com")

    box = ConnectorCredentialBox(tmp_path / "credentials")
    runtime = ConnectorRuntime(box, (), httpx.AsyncClient())
    try:
        with pytest.raises(ValueError, match="allowlist"):
            runtime.require_allowed_origin("https://api.example.test")
    finally:
        await runtime.close()


def test_connector_policy_rejects_noncanonical_path_scope_bypasses(tmp_path):
    connector = _connector(ConnectorCredentialBox(tmp_path / "credentials"))

    ConnectorService._authorize(
        connector,
        "GET",
        "/v1/status",
        action=ConnectorAction.EXECUTE,
    )
    for path in (
        "/v1/%2e%2e/private",
        "/v1/../private",
        "/v1//private",
        "/v1/\nprivate",
    ):
        with pytest.raises(ConnectorPermissionError):
            ConnectorService._authorize(
                connector,
                "GET",
                path,
                action=ConnectorAction.EXECUTE,
            )
