from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.connectors.credentials import (
    ConnectorCredentialBox,
    ConnectorCredentialError,
    OAuth2Credential,
    decode_oauth2_credential,
    encode_oauth2_credential,
)
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecutionStatus,
)


MAX_CONNECTOR_REQUEST_BYTES = 32_768
MAX_CONNECTOR_RESPONSE_BYTES = 262_144
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN_SECONDS = 30.0


def _reject_non_finite_json(_value: str) -> None:
    raise ValueError("non-finite JSON number is not permitted")


class ConnectorRuntimeError(RuntimeError):
    def __init__(
        self,
        status: ConnectorExecutionStatus,
        code: str,
        *,
        attempts: int,
        response_status_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.attempts = attempts
        self.response_status_code = response_status_code


@dataclass(frozen=True, slots=True)
class ConnectorRuntimeResult:
    payload: Any
    response_status_code: int
    request_body_sha256: str | None
    response_body_sha256: str
    response_bytes: int
    attempts: int


def normalize_connector_origin(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 2_048:
        raise ValueError("connector origin is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("connector origin must be an exact credential-free HTTP origin")
    host = parsed.hostname
    loopback = host == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme == "http" and not loopback:
        raise ValueError("non-loopback connector origins must use HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def is_loopback_origin(origin: str) -> bool:
    host = urlsplit(origin).hostname
    if host == "localhost":
        return True
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _ConnectorRateLimiter:
    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def acquire(self, connector_id: str, limit: int) -> bool:
        now = time.monotonic()
        async with self._lock:
            requests = self._requests[connector_id]
            while requests and requests[0] <= now - 60:
                requests.popleft()
            if len(requests) >= limit:
                return False
            requests.append(now)
            return True


class _ConnectorCircuitBreaker:
    def __init__(self) -> None:
        self._failures: dict[str, int] = defaultdict(int)
        self._opened_until: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def is_open(self, connector_id: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            opened_until = self._opened_until.get(connector_id)
            if opened_until is None:
                return False
            if opened_until <= now:
                self._opened_until.pop(connector_id, None)
                self._failures[connector_id] = 0
                return False
            return True

    async def record_failure(self, connector_id: str) -> None:
        async with self._lock:
            failures = self._failures[connector_id] + 1
            self._failures[connector_id] = failures
            if failures >= _CIRCUIT_FAILURE_THRESHOLD:
                self._opened_until[connector_id] = (
                    time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
                )

    async def record_success(self, connector_id: str) -> None:
        async with self._lock:
            self._failures.pop(connector_id, None)
            self._opened_until.pop(connector_id, None)


class ConnectorRuntime:
    """Allowlisted JSON-over-HTTP executor with fixed authentication surfaces."""

    def __init__(
        self,
        credential_box: ConnectorCredentialBox,
        allowed_origins: tuple[str, ...],
        client: httpx.AsyncClient,
    ) -> None:
        normalized = tuple(normalize_connector_origin(item) for item in allowed_origins)
        if len(set(normalized)) != len(normalized):
            raise ValueError("connector allowed origins must be unique")
        self.credential_box = credential_box
        self.allowed_origins = frozenset(normalized)
        self.client = client
        self._rate_limiter = _ConnectorRateLimiter()
        self._circuit_breaker = _ConnectorCircuitBreaker()

    @staticmethod
    def create_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    async def close(self) -> None:
        await self.client.aclose()

    def require_allowed_origin(self, origin: str) -> str:
        normalized = normalize_connector_origin(origin)
        if normalized not in self.allowed_origins:
            raise ValueError("connector origin is not in the operator egress allowlist")
        return normalized

    async def execute(
        self,
        connector: Connector,
        *,
        action: ConnectorAction,
        method: str,
        path: str,
        json_body: Any | None,
        idempotency_key: str | None,
    ) -> ConnectorRuntimeResult:
        connector_id = str(connector.id)
        try:
            self.require_allowed_origin(connector.base_url)
        except ValueError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_permission_denied",
                attempts=0,
            ) from exc
        if (
            action is not ConnectorAction.HEALTH
            and await self._circuit_breaker.is_open(connector_id)
        ):
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_circuit_open",
                attempts=0,
            )
        try:
            result = await self._execute_request(
                connector,
                action=action,
                method=method,
                path=path,
                json_body=json_body,
                idempotency_key=idempotency_key,
            )
        except ConnectorRuntimeError as exc:
            if (
                exc.code
                in {
                    "connector_timed_out",
                    "connector_unavailable",
                    "connector_response_invalid",
                }
                or (
                    exc.code == "connector_http_error"
                    and exc.response_status_code in RETRYABLE_STATUS_CODES
                )
            ):
                await self._circuit_breaker.record_failure(connector_id)
            raise
        await self._circuit_breaker.record_success(connector_id)
        return result

    async def _execute_request(
        self,
        connector: Connector,
        *,
        action: ConnectorAction,
        method: str,
        path: str,
        json_body: Any | None,
        idempotency_key: str | None,
    ) -> ConnectorRuntimeResult:
        body = None
        request_hash = None
        if json_body is not None:
            try:
                body = json.dumps(
                    json_body,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, RecursionError) as exc:
                raise ValueError("connector JSON body is invalid") from exc
            if len(body) > MAX_CONNECTOR_REQUEST_BYTES:
                raise ValueError("connector JSON body exceeds its bound")
            request_hash = hashlib.sha256(body).hexdigest()

        headers = {"Accept": "application/json", "User-Agent": "AI-OS-Connector/1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        headers.update(await self._authentication_headers(connector))

        retry_safe = method in {"GET", "HEAD"} or idempotency_key is not None
        maximum_attempts = 1 + (connector.max_retries if retry_safe else 0)
        url = connector.base_url + path
        last_status = None
        for attempt in range(1, maximum_attempts + 1):
            if not await self._rate_limiter.acquire(
                str(connector.id), connector.rate_limit_requests_per_minute
            ):
                raise ConnectorRuntimeError(
                    ConnectorExecutionStatus.RATE_LIMITED,
                    "connector_rate_limited",
                    attempts=attempt - 1,
                    response_status_code=last_status,
                )
            try:
                async with asyncio.timeout(connector.timeout_seconds):
                    async with self.client.stream(
                        method,
                        url,
                        headers=headers,
                        content=body,
                    ) as response:
                        last_status = response.status_code
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_CONNECTOR_RESPONSE_BYTES:
                                raise ValueError("connector response exceeded its bound")
                if response.status_code in RETRYABLE_STATUS_CODES:
                    if attempt < maximum_attempts:
                        await asyncio.sleep(self._retry_delay(response, attempt))
                        continue
                    raise ConnectorRuntimeError(
                        ConnectorExecutionStatus.FAILED,
                        "connector_http_error",
                        attempts=attempt,
                        response_status_code=response.status_code,
                    )
                if not 200 <= response.status_code <= 299:
                    raise ConnectorRuntimeError(
                        ConnectorExecutionStatus.FAILED,
                        "connector_http_error",
                        attempts=attempt,
                        response_status_code=response.status_code,
                    )
                if method == "HEAD" or response.status_code == 204 or not raw:
                    payload: Any = None
                else:
                    content_type = response.headers.get("content-type", "").lower()
                    if not (
                        content_type.startswith("application/json")
                        or "+json" in content_type.split(";", 1)[0]
                    ):
                        raise ValueError("connector response is not JSON")
                    payload = json.loads(
                        bytes(raw),
                        parse_constant=_reject_non_finite_json,
                    )
                return ConnectorRuntimeResult(
                    payload=payload,
                    response_status_code=response.status_code,
                    request_body_sha256=request_hash,
                    response_body_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
                    response_bytes=len(raw),
                    attempts=attempt,
                )
            except TimeoutError as exc:
                if attempt < maximum_attempts:
                    await asyncio.sleep(0.1 * attempt)
                    continue
                raise ConnectorRuntimeError(
                    ConnectorExecutionStatus.TIMED_OUT,
                    "connector_timed_out",
                    attempts=attempt,
                    response_status_code=last_status,
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < maximum_attempts:
                    await asyncio.sleep(0.1 * attempt)
                    continue
                raise ConnectorRuntimeError(
                    ConnectorExecutionStatus.FAILED,
                    "connector_unavailable",
                    attempts=attempt,
                    response_status_code=last_status,
                ) from exc
            except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
                raise ConnectorRuntimeError(
                    ConnectorExecutionStatus.FAILED,
                    "connector_response_invalid",
                    attempts=attempt,
                    response_status_code=last_status,
                ) from exc
        raise RuntimeError("connector retry loop did not terminate")  # pragma: no cover

    async def _authentication_headers(self, connector: Connector) -> dict[str, str]:
        if connector.credential_ciphertext is None:
            return {}
        try:
            credential = self.credential_box.decrypt(connector.credential_ciphertext)
            oauth2 = decode_oauth2_credential(credential)
        except ConnectorCredentialError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_unavailable",
                attempts=0,
            ) from exc
        if connector.auth_kind in {
            ConnectorAuthKind.OAUTH2_BEARER,
            ConnectorAuthKind.OIDC_BEARER,
        }:
            if oauth2 is not None:
                expires_at = oauth2.expires_at
                if (
                    expires_at is not None
                    and expires_at.astimezone(timezone.utc)
                    <= datetime.now(timezone.utc) + timedelta(seconds=30)
                ):
                    oauth2 = await self._refresh_oauth2(connector, oauth2)
                credential = oauth2.access_token
            return {"Authorization": f"Bearer {credential}"}
        if oauth2 is not None:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_unavailable",
                attempts=0,
            )
        if connector.auth_kind is ConnectorAuthKind.BEARER:
            return {"Authorization": f"Bearer {credential}"}
        if connector.auth_kind is ConnectorAuthKind.API_KEY:
            return {"X-API-Key": credential}
        return {}

    async def _refresh_oauth2(
        self, connector: Connector, credential: OAuth2Credential
    ) -> OAuth2Credential:
        if None in {
            credential.refresh_token,
            credential.client_id,
            credential.client_secret,
            credential.token_path,
        }:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_unavailable",
                attempts=0,
            )
        assert credential.refresh_token is not None
        assert credential.client_id is not None
        assert credential.client_secret is not None
        assert credential.token_path is not None
        try:
            token_origin = self.require_allowed_origin(
                credential.token_origin or connector.base_url
            )
        except ValueError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_permission_denied",
                attempts=0,
            ) from exc
        content = urlencode(
            {
                "client_id": credential.client_id,
                "client_secret": credential.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": credential.refresh_token,
            }
        ).encode("ascii")
        if len(content) > MAX_CONNECTOR_REQUEST_BYTES:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_unavailable",
                attempts=0,
            )
        if not await self._rate_limiter.acquire(
            str(connector.id), connector.rate_limit_requests_per_minute
        ):
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.RATE_LIMITED,
                "connector_rate_limited",
                attempts=0,
            )
        try:
            async with asyncio.timeout(connector.timeout_seconds):
                async with self.client.stream(
                    "POST",
                    token_origin + credential.token_path,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "AI-OS-Connector/1",
                    },
                    content=content,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > MAX_CONNECTOR_RESPONSE_BYTES:
                            raise ValueError("OAuth response exceeded its bound")
        except TimeoutError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.TIMED_OUT,
                "connector_timed_out",
                attempts=1,
            ) from exc
        except httpx.HTTPError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_unavailable",
                attempts=1,
            ) from exc
        except ValueError as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_response_invalid",
                attempts=1,
            ) from exc
        if not 200 <= response.status_code <= 299:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_http_error",
                attempts=1,
                response_status_code=response.status_code,
            )
        try:
            payload = json.loads(bytes(raw), parse_constant=_reject_non_finite_json)
            access_token = payload["access_token"]
            expires_in = payload["expires_in"]
            if (
                not isinstance(payload, dict)
                or not isinstance(access_token, str)
                or isinstance(expires_in, bool)
                or not isinstance(expires_in, int)
                or not 30 <= expires_in <= 86_400
            ):
                raise ValueError
            refresh_token = payload.get("refresh_token", credential.refresh_token)
            refreshed = OAuth2Credential(
                access_token=access_token,
                refresh_token=refresh_token,
                client_id=credential.client_id,
                client_secret=credential.client_secret,
                token_path=credential.token_path,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
                token_origin=credential.token_origin,
            )
            connector.credential_ciphertext = self.credential_box.encrypt(
                encode_oauth2_credential(refreshed)
            )
        except (
            ConnectorCredentialError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RecursionError,
        ) as exc:
            raise ConnectorRuntimeError(
                ConnectorExecutionStatus.FAILED,
                "connector_response_invalid",
                attempts=1,
                response_status_code=response.status_code,
            ) from exc
        return refreshed

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                return min(2.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return 0.1 * attempt
