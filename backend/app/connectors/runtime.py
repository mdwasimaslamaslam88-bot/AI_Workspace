from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.credentials import ConnectorCredentialBox, ConnectorCredentialError
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecutionStatus,
)


MAX_CONNECTOR_REQUEST_BYTES = 32_768
MAX_CONNECTOR_RESPONSE_BYTES = 262_144
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


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
        if connector.credential_ciphertext is not None:
            try:
                credential = self.credential_box.decrypt(connector.credential_ciphertext)
            except ConnectorCredentialError as exc:
                raise ConnectorRuntimeError(
                    ConnectorExecutionStatus.FAILED,
                    "connector_unavailable",
                    attempts=0,
                ) from exc
            if connector.auth_kind in {
                ConnectorAuthKind.BEARER,
                ConnectorAuthKind.OAUTH2_BEARER,
            }:
                headers["Authorization"] = f"Bearer {credential}"
            elif connector.auth_kind is ConnectorAuthKind.API_KEY:
                headers["X-API-Key"] = credential

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

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = response.headers.get("retry-after")
        if raw is not None:
            try:
                return min(2.0, max(0.0, float(raw)))
            except ValueError:
                pass
        return 0.1 * attempt
