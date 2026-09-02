from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.connectors.service import ConnectorConnectionStatus
from app.models.connector import (
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorExecutionStatus,
    ConnectorKind,
)


class ConnectorSettingsResponse(BaseModel):
    configured: bool
    allowed_origins: list[str] = Field(max_length=64)
    supported_kinds: list[ConnectorKind]
    supported_auth_kinds: list[ConnectorAuthKind]


class ConnectorWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")
    kind: ConnectorKind
    base_url: str = Field(min_length=8, max_length=2_048)
    auth_kind: ConnectorAuthKind = ConnectorAuthKind.NONE
    credential: SecretStr | None = Field(default=None, min_length=16, max_length=512)
    scopes: list[Literal["read", "write"]] = Field(min_length=1, max_length=2)
    path_prefixes: list[str] = Field(min_length=1, max_length=16)
    health_path: str = Field(default="/health", min_length=1, max_length=512)
    enabled: bool = False
    timeout_seconds: int = Field(default=5, strict=True, ge=1, le=10)
    max_retries: int = Field(default=1, strict=True, ge=0, le=2)
    rate_limit_requests_per_minute: int = Field(default=30, strict=True, ge=1, le=600)

    @field_validator("scopes", "path_prefixes")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("connector policy values must be unique")
        return value


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    kind: ConnectorKind
    base_url: str
    auth_kind: ConnectorAuthKind
    credential_configured: bool
    scopes: list[Literal["read", "write"]]
    path_prefixes: list[str]
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


class ConnectorPageResponse(BaseModel):
    items: list[ConnectorResponse] = Field(max_length=100)


class ConnectorExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=512)
    json_body: Any | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$",
    )


class ConnectorExecutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class ConnectorExecutionResultResponse(BaseModel):
    execution: ConnectorExecutionResponse
    payload: Any | None


class ConnectorExecutionPageResponse(BaseModel):
    items: list[ConnectorExecutionResponse] = Field(max_length=100)
