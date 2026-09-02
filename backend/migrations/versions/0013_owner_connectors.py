"""Add owner-scoped connector registry and execution audit.

Revision ID: 0013_owner_connectors
Revises: 0012_owner_device_sessions
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_owner_connectors"
down_revision: Union[str, None] = "0012_owner_device_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(*values: str, name: str, length: int = 32) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=length,
    )


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            _enum("rest", "webhook", "local_api", name="connector_kind"),
            nullable=False,
        ),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "auth_kind",
            _enum(
                "none",
                "bearer",
                "api_key",
                "oauth2_bearer",
                name="connector_auth_kind",
            ),
            nullable=False,
        ),
        sa.Column("credential_ciphertext", sa.Text(), nullable=True),
        sa.Column("scopes_json", sa.String(length=128), nullable=False),
        sa.Column("path_prefixes_json", sa.Text(), nullable=False),
        sa.Column("health_path", sa.String(length=512), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("max_retries", sa.SmallInteger(), nullable=False),
        sa.Column(
            "rate_limit_requests_per_minute", sa.SmallInteger(), nullable=False
        ),
        sa.Column(
            "health_status",
            _enum(
                "unknown",
                "healthy",
                "unavailable",
                name="connector_health_status",
            ),
            nullable=False,
        ),
        sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('rest', 'webhook', 'local_api')",
            name=op.f("ck_connectors_kind_allowed"),
        ),
        sa.CheckConstraint(
            "auth_kind IN ('none', 'bearer', 'api_key', 'oauth2_bearer')",
            name=op.f("ck_connectors_auth_kind_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name=op.f("ck_connectors_name_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(base_url) BETWEEN 8 AND 2048",
            name=op.f("ck_connectors_base_url_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(scopes_json) BETWEEN 2 AND 128",
            name=op.f("ck_connectors_scopes_json_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(path_prefixes_json) BETWEEN 5 AND 4096",
            name=op.f("ck_connectors_path_prefixes_json_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(health_path) BETWEEN 1 AND 512",
            name=op.f("ck_connectors_health_path_bounded"),
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 10",
            name=op.f("ck_connectors_timeout_seconds_bounded"),
        ),
        sa.CheckConstraint(
            "max_retries BETWEEN 0 AND 2",
            name=op.f("ck_connectors_max_retries_bounded"),
        ),
        sa.CheckConstraint(
            "rate_limit_requests_per_minute BETWEEN 1 AND 600",
            name=op.f("ck_connectors_rate_limit_bounded"),
        ),
        sa.CheckConstraint(
            "(auth_kind = 'none' AND credential_ciphertext IS NULL) OR "
            "(auth_kind != 'none' AND revoked_at IS NULL AND "
            "char_length(credential_ciphertext) BETWEEN 1 AND 2048) OR "
            "(auth_kind != 'none' AND revoked_at IS NOT NULL AND "
            "credential_ciphertext IS NULL)",
            name=op.f("ck_connectors_credential_state_consistent"),
        ),
        sa.CheckConstraint(
            "health_status IN ('unknown', 'healthy', 'unavailable')",
            name=op.f("ck_connectors_health_status_allowed"),
        ),
        sa.CheckConstraint(
            "(health_status = 'unknown' AND last_health_checked_at IS NULL) OR "
            "(health_status != 'unknown' AND last_health_checked_at IS NOT NULL)",
            name=op.f("ck_connectors_health_state_consistent"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR (enabled = false AND revoked_at >= created_at)",
            name=op.f("ck_connectors_revocation_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_connectors_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connectors")),
        sa.UniqueConstraint("id", "owner_id", name="uq_connectors_id_owner"),
    )
    op.create_table(
        "connector_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("connector_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "action",
            _enum("execute", "health", name="connector_action"),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            _enum(
                "completed",
                "failed",
                "timed_out",
                "rate_limited",
                name="connector_execution_status",
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("request_body_sha256", sa.String(length=64), nullable=True),
        sa.Column("response_body_sha256", sa.String(length=64), nullable=True),
        sa.Column("response_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "action IN ('execute', 'health')",
            name=op.f("ck_connector_executions_action_allowed"),
        ),
        sa.CheckConstraint(
            "method IN ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE')",
            name=op.f("ck_connector_executions_method_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(path) BETWEEN 1 AND 512",
            name=op.f("ck_connector_executions_path_bounded"),
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'timed_out', 'rate_limited')",
            name=op.f("ck_connector_executions_status_allowed"),
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 3",
            name=op.f("ck_connector_executions_attempts_bounded"),
        ),
        sa.CheckConstraint(
            "response_status_code IS NULL OR response_status_code BETWEEN 100 AND 599",
            name=op.f("ck_connector_executions_response_status_bounded"),
        ),
        sa.CheckConstraint(
            "request_body_sha256 IS NULL OR request_body_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_connector_executions_request_hash_valid"),
        ),
        sa.CheckConstraint(
            "response_body_sha256 IS NULL OR response_body_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_connector_executions_response_hash_valid"),
        ),
        sa.CheckConstraint(
            "response_bytes IS NULL OR response_bytes BETWEEN 0 AND 262144",
            name=op.f("ck_connector_executions_response_bytes_bounded"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('connector_disabled', "
            "'connector_permission_denied', 'connector_rate_limited', "
            "'connector_timed_out', 'connector_unavailable', "
            "'connector_http_error', 'connector_response_invalid')",
            name=op.f("ck_connector_executions_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name=op.f("ck_connector_executions_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND response_status_code BETWEEN 200 AND 299 "
            "AND response_body_sha256 IS NOT NULL AND response_bytes IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status != 'completed' AND error_code IS NOT NULL)",
            name=op.f("ck_connector_executions_terminal_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["connector_id", "owner_id"],
            ["connectors.id", "connectors.owner_id"],
            name="fk_connector_executions_connector_owner_connectors",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_connector_executions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_executions")),
    )
    op.create_index(
        "ix_connectors_owner_created_at",
        "connectors",
        ["owner_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_connectors_owner_enabled",
        "connectors",
        ["owner_id", "enabled", "revoked_at"],
        unique=False,
    )
    op.create_index(
        "ix_connector_executions_owner_started_at",
        "connector_executions",
        ["owner_id", sa.literal_column("started_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_connector_executions_connector_started_at",
        "connector_executions",
        ["connector_id", sa.literal_column("started_at").desc()],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_executions_connector_started_at",
        table_name="connector_executions",
    )
    op.drop_index(
        "ix_connector_executions_owner_started_at",
        table_name="connector_executions",
    )
    op.drop_index("ix_connectors_owner_enabled", table_name="connectors")
    op.drop_index("ix_connectors_owner_created_at", table_name="connectors")
    op.drop_table("connector_executions")
    op.drop_table("connectors")
