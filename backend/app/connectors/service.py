from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import re
import time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.credentials import (
    ConnectorCredentialError,
    decode_oauth2_credential,
)
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
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()
_HIGH_IMPACT_BROKER_CAPABILITIES = frozenset(
    {"broker.order.submit", "broker.order.modify", "broker.order.cancel"}
)


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
    provider: str = "custom"
    service: str = "api"
    capabilities: tuple[str, ...] = ("read",)
    permissions: tuple[str, ...] = ("read",)
    discovery_path: str | None = None
    last_successful_test_at: datetime | None = None
    audit_reference: UUID | None = None


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


def _validate_label(value: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= 120
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"connector {label} is invalid")
    return value


def _validate_capabilities(capabilities: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not isinstance(capabilities, tuple)
        or not 1 <= len(capabilities) <= 32
        or len(set(capabilities)) != len(capabilities)
        or any(
            not isinstance(capability, str)
            or _CAPABILITY_PATTERN.fullmatch(capability) is None
            for capability in capabilities
        )
    ):
        raise ValueError("connector capabilities are invalid")
    return tuple(sorted(capabilities))


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
            provider=connector.provider,
            service=connector.service,
            capabilities=_decode_list(connector.capabilities_json),
            permissions=_decode_list(connector.scopes_json),
            discovery_path=connector.discovery_path,
            last_successful_test_at=connector.last_successful_test_at,
            audit_reference=connector.last_audit_reference,
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
        provider: str = "custom",
        service: str = "api",
        capabilities: tuple[str, ...] = ("read",),
        discovery_path: str | None = None,
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
                provider=provider,
                service=service,
                capabilities=capabilities,
                discovery_path=discovery_path,
            )
            self.session.add(connector)
            await self.session.flush()
            management_actions = [ConnectorAction.CONFIGURE]
            if auth_kind is not ConnectorAuthKind.NONE:
                management_actions.append(ConnectorAction.CREDENTIAL_CHANGE)
            management_actions.append(ConnectorAction.PERMISSION_CHANGE)
            await self._record_management_audits(
                connector,
                tuple(management_actions),
                policy_sha256=self._policy_hash(connector),
            )
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
            previous_policy_hash = self._policy_hash(connector)
            previous_permissions = self._permission_policy(connector)
            credential_changed = values.get("credential") is not None
            self._build_connector(owner_id=owner_id, connector=connector, **values)
            next_policy_hash = self._policy_hash(connector)
            management_actions = [ConnectorAction.CONFIGURE]
            if credential_changed:
                management_actions.append(ConnectorAction.CREDENTIAL_CHANGE)
            if previous_permissions != self._permission_policy(connector):
                management_actions.append(ConnectorAction.PERMISSION_CHANGE)
            await self.session.flush()
            await self._record_management_audits(
                connector,
                tuple(management_actions),
                policy_sha256=(
                    next_policy_hash
                    if next_policy_hash != previous_policy_hash
                    else previous_policy_hash
                ),
            )
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
        provider: str = "custom",
        service: str = "api",
        capabilities: tuple[str, ...] = ("read",),
        discovery_path: str | None = None,
    ) -> Connector:
        _validate_label(name, "name")
        validated_provider = _validate_label(provider, "provider")
        validated_service = _validate_label(service, "service")
        if not isinstance(kind, ConnectorKind) or not isinstance(auth_kind, ConnectorAuthKind):
            raise TypeError("connector kind is invalid")
        origin = self.runtime.require_allowed_origin(base_url)
        if kind is ConnectorKind.LOCAL_API and not is_loopback_origin(origin):
            raise ValueError("local API connectors require an exact loopback origin")
        validated_scopes = _validate_scopes(scopes)
        validated_capabilities = _validate_capabilities(capabilities)
        if "read" not in validated_scopes:
            raise ValueError("connector health checks require read scope")
        if kind is ConnectorKind.WEBHOOK and "write" not in validated_scopes:
            raise ValueError("webhook connectors require write scope")
        validated_prefixes = _validate_prefixes(path_prefixes)
        validated_health_path = _validate_path(health_path)
        if not _path_allowed(validated_health_path, validated_prefixes):
            raise ValueError("connector health path exceeds its allowed path prefixes")
        validated_discovery_path = (
            _validate_path(discovery_path) if discovery_path is not None else None
        )
        if validated_discovery_path is not None and not _path_allowed(
            validated_discovery_path, validated_prefixes
        ):
            raise ValueError("connector discovery path exceeds its allowed path prefixes")
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
            try:
                oauth2 = decode_oauth2_credential(credential)
            except ConnectorCredentialError as exc:
                raise ValueError("OAuth credential envelope is invalid") from exc
            if oauth2 is not None:
                if auth_kind not in {
                    ConnectorAuthKind.OAUTH2_BEARER,
                    ConnectorAuthKind.OIDC_BEARER,
                }:
                    raise ValueError("OAuth credentials require an OAuth auth kind")
                if oauth2.token_path is not None:
                    token_path = _validate_path(oauth2.token_path)
                    if oauth2.token_origin is None and not _path_allowed(
                        token_path, validated_prefixes
                    ):
                        raise ValueError(
                            "OAuth token path exceeds its allowed path prefixes"
                        )
                    if oauth2.token_origin is not None:
                        self.runtime.require_allowed_origin(oauth2.token_origin)
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
            "provider": validated_provider,
            "service": validated_service,
            "capabilities_json": _json_list(validated_capabilities),
            "kind": kind,
            "base_url": origin,
            "auth_kind": auth_kind,
            "credential_ciphertext": ciphertext,
            "scopes_json": _json_list(validated_scopes),
            "path_prefixes_json": _json_list(validated_prefixes),
            "health_path": validated_health_path,
            "discovery_path": validated_discovery_path,
            "enabled": enabled,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "rate_limit_requests_per_minute": rate_limit_requests_per_minute,
            "health_status": ConnectorHealthStatus.UNKNOWN,
            "last_health_checked_at": None,
            "last_successful_test_at": (
                connector.last_successful_test_at if connector is not None else None
            ),
            "last_audit_reference": (
                connector.last_audit_reference if connector is not None else None
            ),
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
            await self._record_management_audits(
                connector, (ConnectorAction.REVOKE,)
            )
            await self.session.refresh(connector)
            value = self._view(connector)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def disconnect_for_owner(
        self, owner_id: UUID, connector_id: UUID
    ) -> ConnectorView:
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:
                raise ConnectorNotFoundError("connector not found")
            if connector.revoked_at is not None:
                raise ConnectorConflictError("revoked connectors cannot be disconnected")
            connector.enabled = False
            connector.health_status = ConnectorHealthStatus.UNKNOWN
            connector.last_health_checked_at = None
            connector.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            await self._record_management_audits(
                connector, (ConnectorAction.DISCONNECT,)
            )
            await self.session.refresh(connector)
            value = self._view(connector)
            await self.session.commit()
            return value
        except BaseException:
            await self.session.rollback()
            raise

    async def reconnect_for_owner(
        self, owner_id: UUID, connector_id: UUID
    ) -> ConnectorExecutionResult:
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:
                raise ConnectorNotFoundError("connector not found")
            if connector.revoked_at is not None:
                raise ConnectorConflictError("revoked connectors cannot be reconnected")
            connector.enabled = True
            connector.health_status = ConnectorHealthStatus.UNKNOWN
            connector.last_health_checked_at = None
            connector.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        try:
            result = await self.health_for_owner(owner_id, connector_id)
        except ConnectorExecutionError as exc:
            try:
                connector = await self.repository.get_for_owner(owner_id, connector_id)
                if connector is None:  # pragma: no cover - protected by the prior lookup
                    raise ConnectorNotFoundError("connector not found")
                reconnect_audit = self._copy_view_audit(
                    exc.execution, ConnectorAction.RECONNECT, connector=connector
                )
                self.session.add(reconnect_audit)
                await self.session.flush()
                connector.last_audit_reference = reconnect_audit.id
                await self.session.flush()
                await self.session.commit()
            except BaseException:
                await self.session.rollback()
                raise
            raise
        try:
            connector = await self.repository.get_for_owner(owner_id, connector_id)
            if connector is None:  # pragma: no cover - protected by the prior lookup
                raise ConnectorNotFoundError("connector not found")
            await self._record_management_audits(
                connector, (ConnectorAction.RECONNECT,)
            )
            await self.session.commit()
            return result
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
        required_capability: str | None = None,
    ) -> ConnectorExecutionResult:
        started_at = datetime.now(timezone.utc)
        started = time.monotonic()
        connector = await self.repository.get_for_owner(owner_id, connector_id)
        if connector is None:
            await self.session.rollback()
            raise ConnectorNotFoundError("connector not found")
        method = method.upper()
        try:
            self._authorize(
                connector,
                method,
                path,
                action=action,
                required_capability=required_capability,
            )
            result = await self.runtime.execute(
                connector,
                action=action,
                method=method,
                path=path,
                json_body=json_body,
                idempotency_key=idempotency_key,
            )
            if action is ConnectorAction.DISCOVER:
                try:
                    connector.capabilities_json = _json_list(
                        self._capabilities_from_discovery(result.payload)
                    )
                except ValueError as exc:
                    raise ConnectorRuntimeError(
                        ConnectorExecutionStatus.FAILED,
                        "connector_response_invalid",
                        attempts=result.attempts,
                        response_status_code=result.response_status_code,
                    ) from exc
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
            audit_records = []
            if (
                action is ConnectorAction.HEALTH
                and connector.auth_kind is not ConnectorAuthKind.NONE
            ):
                audit_records.append(
                    self._copy_audit(execution, ConnectorAction.AUTHENTICATE)
                )
            audit_records.append(execution)
            if action is ConnectorAction.HEALTH:
                activating = (
                    connector.enabled
                    and connector.health_status is not ConnectorHealthStatus.HEALTHY
                )
                now = datetime.now(timezone.utc)
                connector.health_status = ConnectorHealthStatus.HEALTHY
                connector.last_health_checked_at = now
                connector.last_successful_test_at = now
                if activating:
                    audit_records.append(
                        self._copy_audit(execution, ConnectorAction.ACTIVATE)
                    )
            self.session.add_all(audit_records)
            await self.session.flush()
            connector.last_audit_reference = audit_records[-1].id
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
            audit_records = []
            if (
                action is ConnectorAction.HEALTH
                and connector.auth_kind is not ConnectorAuthKind.NONE
            ):
                audit_records.append(
                    self._copy_audit(execution, ConnectorAction.AUTHENTICATE)
                )
            audit_records.append(execution)
            self.session.add_all(audit_records)
            await self.session.flush()
            connector.last_audit_reference = audit_records[-1].id
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
            audit_records = []
            if (
                action is ConnectorAction.HEALTH
                and connector.auth_kind is not ConnectorAuthKind.NONE
            ):
                audit_records.append(
                    self._copy_audit(execution, ConnectorAction.AUTHENTICATE)
                )
            audit_records.append(execution)
            self.session.add_all(audit_records)
            if action is ConnectorAction.HEALTH:
                connector.health_status = ConnectorHealthStatus.UNAVAILABLE
                connector.last_health_checked_at = datetime.now(timezone.utc)
            await self.session.flush()
            connector.last_audit_reference = audit_records[-1].id
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

    async def discover_for_owner(
        self, owner_id: UUID, connector_id: UUID
    ) -> ConnectorExecutionResult:
        connector = await self.repository.get_for_owner(owner_id, connector_id)
        if connector is None:
            await self.session.rollback()
            raise ConnectorNotFoundError("connector not found")
        discovery_path = connector.discovery_path
        await self.session.rollback()
        if discovery_path is None:
            raise ConnectorConflictError("connector discovery is not configured")
        return await self.execute_for_owner(
            owner_id,
            connector_id,
            method="GET",
            path=discovery_path,
            json_body=None,
            idempotency_key=None,
            action=ConnectorAction.DISCOVER,
        )

    @staticmethod
    def _capabilities_from_discovery(payload: Any) -> tuple[str, ...]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("capabilities"), list
        ):
            raise ValueError("connector discovery response is invalid")
        return _validate_capabilities(tuple(payload["capabilities"]))

    @staticmethod
    def _authorize(
        connector: Connector,
        method: str,
        path: str,
        *,
        action: ConnectorAction,
        required_capability: str | None = None,
    ) -> None:
        if connector.revoked_at is not None:
            raise ConnectorPermissionError("connector_disabled")
        if (
            not connector.enabled
            and action not in {ConnectorAction.HEALTH, ConnectorAction.DISCOVER}
        ):
            raise ConnectorPermissionError("connector_disabled")
        if (
            action is ConnectorAction.EXECUTE
            and connector.health_status is not ConnectorHealthStatus.HEALTHY
        ):
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
        capabilities = frozenset(_decode_list(connector.capabilities_json))
        if (
            method in _WRITE_METHODS
            and (
                capabilities & _HIGH_IMPACT_BROKER_CAPABILITIES
                or (
                    isinstance(connector.service, str)
                    and connector.service.lower() in {"broker", "trading"}
                )
            )
            and required_capability not in _HIGH_IMPACT_BROKER_CAPABILITIES
        ):
            # Broker mutations are reserved for the finance safety gateway.
            # The generic connector endpoint intentionally cannot invoke them.
            raise ConnectorPermissionError()
        if required_capability is not None:
            if _CAPABILITY_PATTERN.fullmatch(required_capability) is None:
                raise ConnectorPermissionError()
            if required_capability not in capabilities:
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

    @staticmethod
    def _copy_audit(
        execution: ConnectorExecution, action: ConnectorAction
    ) -> ConnectorExecution:
        return ConnectorExecution(
            connector_id=execution.connector_id,
            owner_id=execution.owner_id,
            action=action,
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

    @staticmethod
    def _copy_view_audit(
        execution: ConnectorExecutionView,
        action: ConnectorAction,
        *,
        connector: Connector,
    ) -> ConnectorExecution:
        return ConnectorExecution(
            connector_id=connector.id,
            owner_id=connector.owner_id,
            action=action,
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

    @staticmethod
    def _permission_policy(connector: Connector) -> tuple[str, str, str]:
        return (
            connector.scopes_json,
            connector.path_prefixes_json,
            connector.capabilities_json,
        )

    @staticmethod
    def _policy_hash(connector: Connector) -> str:
        policy = {
            "auth_kind": connector.auth_kind.value,
            "base_url": connector.base_url,
            "capabilities": _decode_list(connector.capabilities_json),
            "discovery_path": connector.discovery_path,
            "enabled": connector.enabled,
            "health_path": connector.health_path,
            "kind": connector.kind.value,
            "max_retries": connector.max_retries,
            "name": connector.name,
            "path_prefixes": _decode_list(connector.path_prefixes_json),
            "provider": connector.provider,
            "rate_limit_requests_per_minute": connector.rate_limit_requests_per_minute,
            "scopes": _decode_list(connector.scopes_json),
            "service": connector.service,
            "timeout_seconds": connector.timeout_seconds,
        }
        raw = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def _record_management_audits(
        self,
        connector: Connector,
        actions: tuple[ConnectorAction, ...],
        *,
        policy_sha256: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        records = [
            ConnectorExecution(
                connector_id=connector.id,
                owner_id=connector.owner_id,
                action=action,
                method="POST",
                path=f"/_lifecycle/{action.value}",
                status=ConnectorExecutionStatus.COMPLETED,
                attempts=0,
                response_status_code=204,
                request_body_sha256=policy_sha256,
                response_body_sha256=_EMPTY_BODY_SHA256,
                response_bytes=0,
                error_code=None,
                started_at=now,
                completed_at=now,
                duration_ms=0,
            )
            for action in actions
        ]
        self.session.add_all(records)
        await self.session.flush()
        connector.last_audit_reference = records[-1].id
        await self.session.flush()
