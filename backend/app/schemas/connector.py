from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.connectors.catalog import ConnectorPlatformSupportStatus
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


class ConnectorPlatformCapabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    status: ConnectorPlatformSupportStatus
    execution_mode: str
    requirement: str | None


class ConnectorPlatformResponse(BaseModel):
    lifecycle: list[str] = Field(min_length=14, max_length=14)
    capabilities: list[ConnectorPlatformCapabilityResponse] = Field(
        min_length=13, max_length=13
    )


class OAuth2CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: SecretStr = Field(min_length=16, max_length=2_048)
    refresh_token: SecretStr | None = Field(default=None, min_length=16, max_length=2_048)
    client_id: str | None = Field(
        default=None, min_length=1, max_length=256, pattern=r"^\S+$"
    )
    client_secret: SecretStr | None = Field(
        default=None, min_length=16, max_length=2_048
    )
    token_origin: str | None = Field(default=None, min_length=8, max_length=2_048)
    token_path: str | None = Field(default=None, min_length=1, max_length=512)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def complete_refresh_contract(self):
        values = (
            self.refresh_token,
            self.client_id,
            self.client_secret,
            self.token_path,
        )
        if any(value is not None for value in values) and any(
            value is None for value in values
        ):
            raise ValueError("OAuth refresh configuration must be complete")
        if self.token_origin is not None and any(value is None for value in values):
            raise ValueError("OAuth token origin requires refresh configuration")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("OAuth expiry must be timezone-aware")
        return self


class ConnectorWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$")
    provider: str = Field(
        default="custom", min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$"
    )
    service: str = Field(
        default="api", min_length=1, max_length=120, pattern=r"^\S(?:.*\S)?$"
    )
    kind: ConnectorKind
    base_url: str = Field(min_length=8, max_length=2_048)
    auth_kind: ConnectorAuthKind = ConnectorAuthKind.NONE
    credential: SecretStr | None = Field(default=None, min_length=16, max_length=4_096)
    oauth2_credential: OAuth2CredentialRequest | None = None
    scopes: list[Literal["read", "write"]] = Field(min_length=1, max_length=2)
    capabilities: list[str] = Field(default=["read"], min_length=1, max_length=32)
    path_prefixes: list[str] = Field(min_length=1, max_length=16)
    health_path: str = Field(default="/health", min_length=1, max_length=512)
    discovery_path: str | None = Field(default=None, min_length=1, max_length=512)
    enabled: bool = False
    timeout_seconds: int = Field(default=5, strict=True, ge=1, le=10)
    max_retries: int = Field(default=1, strict=True, ge=0, le=2)
    rate_limit_requests_per_minute: int = Field(default=30, strict=True, ge=1, le=600)

    @field_validator("scopes", "capabilities", "path_prefixes")
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("connector policy values must be unique")
        return value

    @model_validator(mode="after")
    def validate_authentication_contract(self):
        oauth_auth = self.auth_kind in {
            ConnectorAuthKind.OAUTH2_BEARER,
            ConnectorAuthKind.OIDC_BEARER,
        }
        if self.oauth2_credential is not None and not oauth_auth:
            raise ValueError("OAuth credentials require an OAuth auth kind")
        if self.oauth2_credential is not None and self.credential is not None:
            raise ValueError("only one credential input may be supplied")
        if self.auth_kind is ConnectorAuthKind.NONE and (
            self.credential is not None or self.oauth2_credential is not None
        ):
            raise ValueError("credential-free connectors must not receive credentials")
        return self


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider: str
    service: str
    kind: ConnectorKind
    base_url: str
    auth_kind: ConnectorAuthKind
    credential_configured: bool
    scopes: list[Literal["read", "write"]]
    permissions: list[Literal["read", "write"]]
    capabilities: list[str] = Field(min_length=1, max_length=32)
    path_prefixes: list[str]
    health_path: str
    discovery_path: str | None
    enabled: bool
    connection_status: ConnectorConnectionStatus
    timeout_seconds: int
    max_retries: int
    rate_limit_requests_per_minute: int
    last_health_checked_at: datetime | None
    last_successful_test_at: datetime | None
    audit_reference: UUID | None
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
