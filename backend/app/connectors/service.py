from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.runtime import (
    ConnectorRuntime,
    ConnectorRuntimeError,
    is_loopback_origin,
)
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecution,
    ConnectorExecutionStatus,
    ConnectorHealthStatus,
    ConnectorKind,
)
from app.repositories.connector import ConnectorRepository


MAX_CONNECTORS_PER_OWNER = 32
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})
_WRITE_METHODS = _METHODS - _READ_METHODS
_SCOPES = frozenset({"read", "write"})


class ConnectorConnectionStatus(StrEnum):
    REVOKED = "revoked"
    DISABLED = "disabled"
    READY = "ready"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class ConnectorNotFoundError(RuntimeError):
    """The connector does not exist or belongs to another owner."""


class ConnectorConflictError(RuntimeError):
    """The connector cannot execute in its current state."""


class ConnectorPermissionError(RuntimeError):
    """The action exceeds the connector's owner-approved scope."""

    def __init__(self, code: str = "connector_permission_denied") -> None:
        super().__init__(code)
        self.code = code


class ConnectorExecutionError(RuntimeError):
    def __init__(self, execution: "ConnectorExecutionView") -> None:
        super().__init__(execution.error_code or "connector_execution_failed")
        self.execution = execution


@dataclass(frozen=True, slots=True)
class ConnectorView:
    id: UUID
    name: str
    kind: ConnectorKind
    base_url: str
    auth_kind: ConnectorAuthKind
    credential_configured: bool
    scopes: tuple[str, ...]
    path_prefixes: tuple[str, ...]
    health_path: str
    enabled: bool
    connection_status: ConnectorConnectionStatus
    timeout_seconds: int
    max_retries: int
    rate_limit_requests_per_minute: int
    last_health_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConnectorExecutionView:
    id: UUID
    connector_id: UUID
    action: ConnectorAction
    method: str
    path: str
    status: ConnectorExecutionStatus
    attempts: int
    response_status_code: int | None
    request_body_sha256: str | None
    response_body_sha256: str | None
    response_bytes: int | None
    error_code: str | None
    started_at: datetime
    completed_at: datetime
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ConnectorExecutionResult:
    execution: ConnectorExecutionView
    payload: Any


def _json_list(values: tuple[str, ...]) -> str:
    return json.dumps(values, separators=(",", ":"))


def _decode_list(raw: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError("persisted connector policy is invalid")
    return tuple(value)


def _validate_scopes(scopes: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not isinstance(scopes, tuple)
        or not scopes
        or len(scopes) > len(_SCOPES)
        or any(scope not in _SCOPES for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        raise ValueError("connector scopes are invalid")
    return tuple(sorted(scopes))


def _validate_path(path: str, *, prefix: bool = False) -> str:
    if (
        not isinstance(path, str)
        or path != path.strip()
        or not 1 <= len(path) <= 512
        or not path.startswith("/")
        or "?" in path
        or "#" in path
        or "%" in path
        or "\\" in path
        or "//" in path
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
        or (prefix and not path.endswith("/"))
    ):
        raise ValueError("connector path is invalid")
    return path


def _validate_prefixes(prefixes: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not isinstance(prefixes, tuple)
        or not 1 <= len(prefixes) <= 16
        or len(set(prefixes)) != len(prefixes)
    ):
        raise ValueError("connector path prefixes are invalid")
    return tuple(sorted(_validate_path(item, prefix=True) for item in prefixes))


def _path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix[:-1] or path.startswith(prefix) for prefix in prefixes)


class ConnectorService:
    def __init__(self, session: AsyncSession, runtime: ConnectorRuntime) -> None:
        self.session = session
        self.repository = ConnectorRepository(session)
        self.runtime = runtime

    @staticmethod
    def _view(connector: Connector) -> ConnectorView:
        if connector.revoked_at is not None:
            connection_status = ConnectorConnectionStatus.REVOKED
        elif not connector.enabled:
            connection_status = ConnectorConnectionStatus.DISABLED
        elif connector.health_status is ConnectorHealthStatus.HEALTHY:
            connection_status = ConnectorConnectionStatus.HEALTHY
        elif connector.health_status is ConnectorHealthStatus.UNAVAILABLE:
            connection_status = ConnectorConnectionStatus.UNAVAILABLE
        else:
            connection_status = ConnectorConnectionStatus.READY
        return ConnectorView(
            id=connector.id,
            name=connector.name,
            kind=connector.kind,
            base_url=connector.base_url,
            auth_kind=connector.auth_kind,
            credential_configured=connector.credential_ciphertext is not None,
            scopes=_decode_list(connector.scopes_json),
            path_prefixes=_decode_list(connector.path_prefixes_json),
            health_path=connector.health_path,
            enabled=connector.enabled,
            connection_status=connection_status,
            timeout_seconds=connector.timeout_seconds,
            max_retries=connector.max_retries,
            rate_limit_requests_per_minute=connector.rate_limit_requests_per_minute,
            last_health_checked_at=connector.last_health_checked_at,
            created_at=connector.created_at,
            updated_at=connector.updated_at,
            revoked_at=connector.revoked_at,
        )

    @staticmethod
    def _execution_view(execution: ConnectorExecution) -> ConnectorExecutionView:
        return ConnectorExecutionView(
            id=execution.id,
            connector_id=execution.connector_id,
            action=execution.action,
            method=execution.method,
            path=execution.path,
            status=execution.status,
            attempts=execution.attempts,
            response_status_code=execution.response_status_code,
            request_body_sha256=execution.request_body_sha256,
            response_body_sha256=execution.response_body_sha256,
            response_bytes=execution.response_bytes,
            error_code=execution.error_code,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            duration_ms=execution.duration_ms,
        )

    async def list_for_owner(self, owner_id: UUID) -> tuple[ConnectorView, ...]:
        try:
            records = await self.repository.list_for_owner(owner_id)
            values = tuple(self._view(record) for record in records)
            await self.session.rollback()
            return values
        except BaseException:
            await self.session.rollback()
            raise

    async def get_for_owner(self, owner_id: UUID, connector_id: UUID) -> ConnectorView:
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:
                raise ConnectorNotFoundError("connector not found")
            value = self._view(connector)
            await self.session.rollback()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def create_for_owner(
        self,
        owner_id: UUID,
        *,
        name: str,
        kind: ConnectorKind,
        base_url: str,
        auth_kind: ConnectorAuthKind,
        credential: str | None,
        scopes: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        health_path: str,
        enabled: bool,
        timeout_seconds: int,
        max_retries: int,
        rate_limit_requests_per_minute: int,
    ) -> ConnectorView:
        try:
            connector_count = await self.repository.lock_owner_and_count_connectors(
                owner_id
            )
            if connector_count is None:
                raise ConnectorNotFoundError("connector owner not found")
            if connector_count >= MAX_CONNECTORS_PER_OWNER:
                raise ConnectorConflictError("connector registry is full")
            connector = self._build_connector(
                owner_id=owner_id,
                connector=None,
                name=name,
                kind=kind,
                base_url=base_url,
                auth_kind=auth_kind,
                credential=credential,
                scopes=scopes,
                path_prefixes=path_prefixes,
                health_path=health_path,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                rate_limit_requests_per_minute=rate_limit_requests_per_minute,
            )
            self.session.add(connector)
            await self.session.flush()
            await self.session.refresh(connector)
            value = self._view(connector)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def update_for_owner(
        self,
        owner_id: UUID,
        connector_id: UUID,
        **values,
    ) -> ConnectorView:
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:
                raise ConnectorNotFoundError("connector not found")
            if connector.revoked_at is not None:
                raise ConnectorConflictError("revoked connectors cannot be modified")
            self._build_connector(owner_id=owner_id, connector=connector, **values)
            await self.session.flush()
            await self.session.refresh(connector)
            value = self._view(connector)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    def _build_connector(
        self,
        *,
        owner_id: UUID,
        connector: Connector | None,
        name: str,
        kind: ConnectorKind,
        base_url: str,
        auth_kind: ConnectorAuthKind,
        credential: str | None,
        scopes: tuple[str, ...],
        path_prefixes: tuple[str, ...],
        health_path: str,
        enabled: bool,
        timeout_seconds: int,
        max_retries: int,
        rate_limit_requests_per_minute: int,
    ) -> Connector:
        if not isinstance(name, str) or name != name.strip() or not 1 <= len(name) <= 120:
            raise ValueError("connector name is invalid")
        if not isinstance(kind, ConnectorKind) or not isinstance(auth_kind, ConnectorAuthKind):
            raise TypeError("connector kind is invalid")
        origin = self.runtime.require_allowed_origin(base_url)
        if kind is ConnectorKind.LOCAL_API and not is_loopback_origin(origin):
            raise ValueError("local API connectors require an exact loopback origin")
        validated_scopes = _validate_scopes(scopes)
        if "read" not in validated_scopes:
            raise ValueError("connector health checks require read scope")
        if kind is ConnectorKind.WEBHOOK and "write" not in validated_scopes:
            raise ValueError("webhook connectors require write scope")
        validated_prefixes = _validate_prefixes(path_prefixes)
        validated_health_path = _validate_path(health_path)
        if not _path_allowed(validated_health_path, validated_prefixes):
            raise ValueError("connector health path exceeds its allowed path prefixes")
        if any(isinstance(item, bool) for item in (
            timeout_seconds, max_retries, rate_limit_requests_per_minute
        )) or not (
            isinstance(timeout_seconds, int)
            and 1 <= timeout_seconds <= 10
            and isinstance(max_retries, int)
            and 0 <= max_retries <= 2
            and isinstance(rate_limit_requests_per_minute, int)
            and 1 <= rate_limit_requests_per_minute <= 600
        ):
            raise ValueError("connector execution policy is invalid")
        if not isinstance(enabled, bool):
            raise TypeError("connector enabled state must be boolean")
        if auth_kind is ConnectorAuthKind.NONE:
            if credential is not None:
                raise ValueError("credential-free connectors must not receive a credential")
            ciphertext = None
        elif credential is not None:
            ciphertext = self.runtime.credential_box.encrypt(credential)
        elif connector is not None and connector.auth_kind is auth_kind:
            ciphertext = connector.credential_ciphertext
        else:
            raise ValueError("authenticated connectors require a credential")
        if auth_kind is not ConnectorAuthKind.NONE and ciphertext is None:
            raise ValueError("authenticated connectors require a credential")
        fields = {
            "owner_id": owner_id,
            "name": name,
            "kind": kind,
            "base_url": origin,
            "auth_kind": auth_kind,
            "credential_ciphertext": ciphertext,
            "scopes_json": _json_list(validated_scopes),
            "path_prefixes_json": _json_list(validated_prefixes),
            "health_path": validated_health_path,
            "enabled": enabled,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "rate_limit_requests_per_minute": rate_limit_requests_per_minute,
            "health_status": ConnectorHealthStatus.UNKNOWN,
            "last_health_checked_at": None,
        }
        if connector is None:
            return Connector(**fields)
        for field, value in fields.items():
            setattr(connector, field, value)
        return connector

    async def revoke_for_owner(self, owner_id: UUID, connector_id: UUID) -> ConnectorView:
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:
                raise ConnectorNotFoundError("connector not found")
            if connector.revoked_at is None:
                now = datetime.now(timezone.utc)
                connector.enabled = False
                connector.credential_ciphertext = None
                connector.revoked_at = now
                connector.updated_at = now
            await self.session.flush()
            value = self._view(connector)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def list_executions_for_owner(
        self,
        owner_id: UUID,
        *,
        connector_id: UUID | None = None,
        limit: int = 50,
    ) -> tuple[ConnectorExecutionView, ...]:
        try:
            if connector_id is not None and await self.repository.get_for_owner(
                owner_id, connector_id
            ) is None:
                raise ConnectorNotFoundError("connector not found")
            records = await self.repository.list_executions_for_owner(
                owner_id, connector_id=connector_id, limit=limit
            )
            values = tuple(self._execution_view(record) for record in records)
            await self.session.rollback()
            return values
        except BaseException:
            await self.session.rollback()
            raise

    async def execute_for_owner(
        self,
        owner_id: UUID,
        connector_id: UUID,
        *,
        method: str,
        path: str,
        json_body: Any | None,
        idempotency_key: str | None,
        action: ConnectorAction = ConnectorAction.EXECUTE,
    ) -> ConnectorExecutionResult:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        connector = await self.repository.get_for_owner(owner_id, connector_id)
        if connector is None:
            await self.session.rollback()
            raise ConnectorNotFoundError("connector not found")
        method = method.upper()
        try:
            self._authorize(connector, method, path, action=action)
            result = await self.runtime.execute(
                connector,
                action=action,
                method=method,
                path=path,
                json_body=json_body,
                idempotency_key=idempotency_key,
            )
            execution = self._audit(
                connector,
                action=action,
                method=method,
                path=path,
                status=ConnectorExecutionStatus.COMPLETED,
                attempts=result.attempts,
                response_status_code=result.response_status_code,
                request_body_sha256=result.request_body_sha256,
                response_body_sha256=result.response_body_sha256,
                response_bytes=result.response_bytes,
                error_code=None,
                started_at=started_at,
                started=started,
            )
            self.session.add(execution)
            if action is ConnectorAction.HEALTH:
                connector.health_status = ConnectorHealthStatus.HEALTHY
                connector.last_health_checked_at = datetime.now(timezone.utc)
            await self.session.flush()
            view = self._execution_view(execution)
            await self.session.commit()
            return ConnectorExecutionResult(view, result.payload)
        except ConnectorPermissionError as exc:
            execution = self._audit(
                connector,
                action=action,
                method=method if method in _METHODS else "GET",
                path=path if isinstance(path, str) and 1 <= len(path) <= 512 else "/",
                status=ConnectorExecutionStatus.FAILED,
                attempts=0,
                response_status_code=None,
                request_body_sha256=self._request_hash(json_body),
                response_body_sha256=None,
                response_bytes=None,
                error_code=exc.code,
                started_at=started_at,
                started=started,
            )
            self.session.add(execution)
            await self.session.flush()
            await self.session.commit()
            raise ConnectorExecutionError(self._execution_view(execution)) from None
        except ConnectorRuntimeError as exc:
            execution = self._audit(
                connector,
                action=action,
                method=method,
                path=path,
                status=exc.status,
                attempts=exc.attempts,
                response_status_code=exc.response_status_code,
                request_body_sha256=self._request_hash(json_body),
                response_body_sha256=None,
                response_bytes=None,
                error_code=exc.code,
                started_at=started_at,
                started=started,
            )
            self.session.add(execution)
            if action is ConnectorAction.HEALTH:
                connector.health_status = ConnectorHealthStatus.UNAVAILABLE
                connector.last_health_checked_at = datetime.now(timezone.utc)
            await self.session.flush()
            await self.session.commit()
            raise ConnectorExecutionError(self._execution_view(execution)) from None
        except BaseException:
            await self.session.rollback()
            raise

    async def health_for_owner(
        self, owner_id: UUID, connector_id: UUID
    ) -> ConnectorExecutionResult:
        connector = await self.repository.get_for_owner(owner_id, connector_id)
        if connector is None:
            await self.session.rollback()
            raise ConnectorNotFoundError("connector not found")
        health_path = connector.health_path
        await self.session.rollback()
        return await self.execute_for_owner(
            owner_id,
            connector_id,
            method="GET",
            path=health_path,
            json_body=None,
            idempotency_key=None,
            action=ConnectorAction.HEALTH,
        )

    @staticmethod
    def _authorize(
        connector: Connector,
        method: str,
        path: str,
        *,
        action: ConnectorAction,
    ) -> None:
        if connector.revoked_at is not None or not connector.enabled:
            raise ConnectorPermissionError("connector_disabled")
        if method not in _METHODS:
            raise ConnectorPermissionError()
        try:
            validated_path = _validate_path(path)
        except ValueError:
            raise ConnectorPermissionError() from None
        prefixes = _decode_list(connector.path_prefixes_json)
        if not _path_allowed(validated_path, prefixes):
            raise ConnectorPermissionError()
        scopes = frozenset(_decode_list(connector.scopes_json))
        required_scope = "read" if method in _READ_METHODS else "write"
        if required_scope not in scopes:
            raise ConnectorPermissionError()
        if connector.kind is ConnectorKind.WEBHOOK and (
            action is ConnectorAction.EXECUTE and method != "POST"
        ):
            raise ConnectorPermissionError()

    @staticmethod
    def _request_hash(value: Any | None) -> str | None:
        if value is None:
            return None
        try:
            raw = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            return None
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _audit(
        connector: Connector,
        *,
        action: ConnectorAction,
        method: str,
        path: str,
        status: ConnectorExecutionStatus,
        attempts: int,
        response_status_code: int | None,
        request_body_sha256: str | None,
        response_body_sha256: str | None,
        response_bytes: int | None,
        error_code: str | None,
        started_at: datetime,
        started: float,
    ) -> ConnectorExecution:
        return ConnectorExecution(
            connector_id=connector.id,
            owner_id=connector.owner_id,
            action=action,
            method=method,
            path=path,
            status=status,
            attempts=attempts,
            response_status_code=response_status_code,
            request_body_sha256=request_body_sha256,
            response_body_sha256=response_body_sha256,
            response_bytes=response_bytes,
            error_code=error_code,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
        )
