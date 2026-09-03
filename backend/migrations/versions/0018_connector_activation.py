"""Add production connector activation metadata and lifecycle evidence.

Revision ID: 0018_connector_activation
Revises: 0017_creative_experiences
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_connector_activation"
down_revision: Union[str, None] = "0017_creative_experiences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column(
            "provider",
            sa.String(length=120),
            server_default="custom",
            nullable=False,
        ),
    )
    op.add_column(
        "connectors",
        sa.Column(
            "service",
            sa.String(length=120),
            server_default="api",
            nullable=False,
        ),
    )
    op.add_column(
        "connectors",
        sa.Column(
            "capabilities_json",
            sa.Text(),
            server_default='["read"]',
            nullable=False,
        ),
    )
    op.add_column(
        "connectors",
        sa.Column("discovery_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "connectors",
        sa.Column("last_successful_test_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "connectors",
        sa.Column("last_audit_reference", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.alter_column("connectors", "provider", server_default=None)
    op.alter_column("connectors", "service", server_default=None)
    op.alter_column("connectors", "capabilities_json", server_default=None)
    op.create_check_constraint(
        op.f("ck_connectors_provider_bounded_nonblank"),
        "connectors",
        "char_length(trim(provider)) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        op.f("ck_connectors_service_bounded_nonblank"),
        "connectors",
        "char_length(trim(service)) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        op.f("ck_connectors_capabilities_json_bounded"),
        "connectors",
        "char_length(capabilities_json) BETWEEN 4 AND 4096",
    )
    op.create_check_constraint(
        op.f("ck_connectors_discovery_path_bounded"),
        "connectors",
        "discovery_path IS NULL OR char_length(discovery_path) BETWEEN 1 AND 512",
    )

    op.drop_constraint(
        op.f("ck_connectors_kind_allowed"), "connectors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_connectors_kind_allowed"),
        "connectors",
        "kind IN ('rest', 'graphql', 'webhook', 'local_api')",
    )
    op.drop_constraint(
        op.f("ck_connectors_auth_kind_allowed"), "connectors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_connectors_auth_kind_allowed"),
        "connectors",
        "auth_kind IN ('none', 'bearer', 'api_key', 'oauth2_bearer', 'oidc_bearer')",
    )
    op.drop_constraint(
        op.f("ck_connectors_credential_state_consistent"),
        "connectors",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connectors_credential_state_consistent"),
        "connectors",
        "(auth_kind = 'none' AND credential_ciphertext IS NULL) OR "
        "(auth_kind != 'none' AND revoked_at IS NULL AND "
        "char_length(credential_ciphertext) BETWEEN 1 AND 8192) OR "
        "(auth_kind != 'none' AND revoked_at IS NOT NULL AND "
        "credential_ciphertext IS NULL)",
    )

    op.drop_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        "action IN ('discover', 'execute', 'health')",
    )
    op.drop_constraint(
        op.f("ck_connector_executions_error_code_allowed"),
        "connector_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connector_executions_error_code_allowed"),
        "connector_executions",
        "error_code IS NULL OR error_code IN ('connector_disabled', "
        "'connector_permission_denied', 'connector_rate_limited', "
        "'connector_timed_out', 'connector_unavailable', "
        "'connector_http_error', 'connector_response_invalid', "
        "'connector_circuit_open')",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM connector_executions WHERE action = 'discover' "
        "OR error_code = 'connector_circuit_open'"
    )
    op.execute("UPDATE connectors SET kind = 'rest' WHERE kind = 'graphql'")
    op.execute(
        "UPDATE connectors SET auth_kind = 'oauth2_bearer' "
        "WHERE auth_kind = 'oidc_bearer'"
    )

    op.drop_constraint(
        op.f("ck_connector_executions_error_code_allowed"),
        "connector_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connector_executions_error_code_allowed"),
        "connector_executions",
        "error_code IS NULL OR error_code IN ('connector_disabled', "
        "'connector_permission_denied', 'connector_rate_limited', "
        "'connector_timed_out', 'connector_unavailable', "
        "'connector_http_error', 'connector_response_invalid')",
    )
    op.drop_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        "action IN ('execute', 'health')",
    )

    op.drop_constraint(
        op.f("ck_connectors_credential_state_consistent"),
        "connectors",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connectors_credential_state_consistent"),
        "connectors",
        "(auth_kind = 'none' AND credential_ciphertext IS NULL) OR "
        "(auth_kind != 'none' AND revoked_at IS NULL AND "
        "char_length(credential_ciphertext) BETWEEN 1 AND 2048) OR "
        "(auth_kind != 'none' AND revoked_at IS NOT NULL AND "
        "credential_ciphertext IS NULL)",
    )
    op.drop_constraint(
        op.f("ck_connectors_auth_kind_allowed"), "connectors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_connectors_auth_kind_allowed"),
        "connectors",
        "auth_kind IN ('none', 'bearer', 'api_key', 'oauth2_bearer')",
    )
    op.drop_constraint(
        op.f("ck_connectors_kind_allowed"), "connectors", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_connectors_kind_allowed"),
        "connectors",
        "kind IN ('rest', 'webhook', 'local_api')",
    )

    op.drop_constraint(
        op.f("ck_connectors_discovery_path_bounded"), "connectors", type_="check"
    )
    op.drop_constraint(
        op.f("ck_connectors_capabilities_json_bounded"),
        "connectors",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_connectors_service_bounded_nonblank"),
        "connectors",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_connectors_provider_bounded_nonblank"),
        "connectors",
        type_="check",
    )
    op.drop_column("connectors", "last_audit_reference")
    op.drop_column("connectors", "last_successful_test_at")
    op.drop_column("connectors", "discovery_path")
    op.drop_column("connectors", "capabilities_json")
    op.drop_column("connectors", "service")
    op.drop_column("connectors", "provider")
