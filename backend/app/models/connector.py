from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConnectorKind(StrEnum):
    REST = "rest"
    GRAPHQL = "graphql"
    WEBHOOK = "webhook"
    LOCAL_API = "local_api"


class ConnectorAuthKind(StrEnum):
    NONE = "none"
    BEARER = "bearer"
    API_KEY = "api_key"
    OAUTH2_BEARER = "oauth2_bearer"
    OIDC_BEARER = "oidc_bearer"


class ConnectorHealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class ConnectorAction(StrEnum):
    CONFIGURE = "configure"
    CREDENTIAL_CHANGE = "credential_change"
    PERMISSION_CHANGE = "permission_change"
    AUTHENTICATE = "authenticate"
    DISCOVER = "discover"
    EXECUTE = "execute"
    HEALTH = "health"
    ACTIVATE = "activate"
    DISCONNECT = "disconnect"
    RECONNECT = "reconnect"
    REVOKE = "revoke"


class ConnectorExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RATE_LIMITED = "rate_limited"


def _enum(enum_type, name: str, length: int = 32):
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda value: [member.value for member in value],
        length=length,
    )


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('rest', 'graphql', 'webhook', 'local_api')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "auth_kind IN ('none', 'bearer', 'api_key', 'oauth2_bearer', "
            "'oidc_bearer')",
            name="auth_kind_allowed",
        ),
        CheckConstraint(
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name="name_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(provider)) BETWEEN 1 AND 120",
            name="provider_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(service)) BETWEEN 1 AND 120",
            name="service_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(capabilities_json) BETWEEN 4 AND 4096",
            name="capabilities_json_bounded",
        ),
        CheckConstraint(
            "char_length(base_url) BETWEEN 8 AND 2048",
            name="base_url_bounded",
        ),
        CheckConstraint(
            "char_length(scopes_json) BETWEEN 2 AND 128",
            name="scopes_json_bounded",
        ),
        CheckConstraint(
            "char_length(path_prefixes_json) BETWEEN 5 AND 4096",
            name="path_prefixes_json_bounded",
        ),
        CheckConstraint(
            "char_length(health_path) BETWEEN 1 AND 512",
            name="health_path_bounded",
        ),
        CheckConstraint(
            "discovery_path IS NULL OR char_length(discovery_path) BETWEEN 1 AND 512",
            name="discovery_path_bounded",
        ),
        CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 10",
            name="timeout_seconds_bounded",
        ),
        CheckConstraint(
            "max_retries BETWEEN 0 AND 2",
            name="max_retries_bounded",
        ),
        CheckConstraint(
            "rate_limit_requests_per_minute BETWEEN 1 AND 600",
            name="rate_limit_bounded",
        ),
        CheckConstraint(
            "(auth_kind = 'none' AND credential_ciphertext IS NULL) OR "
            "(auth_kind != 'none' AND revoked_at IS NULL AND "
            "char_length(credential_ciphertext) BETWEEN 1 AND 8192) OR "
            "(auth_kind != 'none' AND revoked_at IS NOT NULL AND "
            "credential_ciphertext IS NULL)",
            name="credential_state_consistent",
        ),
        CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'unavailable')",
            name="health_status_allowed",
        ),
        CheckConstraint(
            "(health_status = 'unknown' AND last_health_checked_at IS NULL) OR "
            "(health_status != 'unknown' AND last_health_checked_at IS NOT NULL)",
            name="health_state_consistent",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR (enabled = false AND revoked_at >= created_at)",
            name="revocation_state_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_connectors_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[ConnectorKind] = mapped_column(
        _enum(ConnectorKind, "connector_kind"), nullable=False
    )
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    auth_kind: Mapped[ConnectorAuthKind] = mapped_column(
        _enum(ConnectorAuthKind, "connector_auth_kind"), nullable=False
    )
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    scopes_json: Mapped[str] = mapped_column(String(128), nullable=False)
    path_prefixes_json: Mapped[str] = mapped_column(Text, nullable=False)
    health_path: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timeout_seconds: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=5)
    max_retries: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    rate_limit_requests_per_minute: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30
    )
    health_status: Mapped[ConnectorHealthStatus] = mapped_column(
        _enum(ConnectorHealthStatus, "connector_health_status"),
        nullable=False,
        default=ConnectorHealthStatus.UNKNOWN,
    )
    last_health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(120), nullable=False, default="custom")
    service: Mapped[str] = mapped_column(String(120), nullable=False, default="api")
    capabilities_json: Mapped[str] = mapped_column(
        Text, nullable=False, default='["read"]'
    )
    discovery_path: Mapped[str | None] = mapped_column(String(512))
    last_successful_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_audit_reference: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class ConnectorExecution(Base):
    __tablename__ = "connector_executions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('configure', 'credential_change', 'permission_change', "
            "'authenticate', 'discover', 'execute', 'health', 'activate', "
            "'disconnect', 'reconnect', 'revoke')",
            name="action_allowed",
        ),
        CheckConstraint(
            "method IN ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE')",
            name="method_allowed",
        ),
        CheckConstraint("char_length(path) BETWEEN 1 AND 512", name="path_bounded"),
        CheckConstraint(
            "status IN ('completed', 'failed', 'timed_out', 'rate_limited')",
            name="status_allowed",
        ),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="attempts_bounded"),
        CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name="response_status_bounded",
        ),
        CheckConstraint(
            "request_body_sha256 IS NULL OR request_body_sha256 ~ '^[0-9a-f]{64}$'",
            name="request_hash_valid",
        ),
        CheckConstraint(
            "response_body_sha256 IS NULL OR response_body_sha256 ~ '^[0-9a-f]{64}$'",
            name="response_hash_valid",
        ),
        CheckConstraint(
            "response_bytes IS NULL OR response_bytes BETWEEN 0 AND 262144",
            name="response_bytes_bounded",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('connector_disabled', "
            "'connector_permission_denied', "
            "'connector_rate_limited', 'connector_timed_out', "
            "'connector_unavailable', 'connector_http_error', "
            "'connector_response_invalid', 'connector_circuit_open')",
            name="error_code_allowed",
        ),
        CheckConstraint("duration_ms >= 0", name="duration_nonnegative"),
        CheckConstraint(
            "(status = 'completed' AND response_status_code BETWEEN 200 AND 299 "
            "AND response_body_sha256 IS NOT NULL AND response_bytes IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status != 'completed' AND error_code IS NOT NULL)",
            name="terminal_state_consistent",
        ),
        ForeignKeyConstraint(
            ["connector_id", "owner_id"],
            ["connectors.id", "connectors.owner_id"],
            name="fk_connector_executions_connector_owner_connectors",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    connector_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[ConnectorAction] = mapped_column(
        _enum(ConnectorAction, "connector_action"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[ConnectorExecutionStatus] = mapped_column(
        _enum(ConnectorExecutionStatus, "connector_execution_status"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    response_status_code: Mapped[int | None] = mapped_column(Integer)
    request_body_sha256: Mapped[str | None] = mapped_column(String(64))
    response_body_sha256: Mapped[str | None] = mapped_column(String(64))
    response_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


Index("ix_connectors_owner_created_at", Connector.owner_id, Connector.created_at.desc())
Index("ix_connectors_owner_enabled", Connector.owner_id, Connector.enabled, Connector.revoked_at)
Index(
    "ix_connector_executions_owner_started_at",
    ConnectorExecution.owner_id,
    ConnectorExecution.started_at.desc(),
)
Index(
    "ix_connector_executions_connector_started_at",
    ConnectorExecution.connector_id,
    ConnectorExecution.started_at.desc(),
)
