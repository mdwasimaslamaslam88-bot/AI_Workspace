"""Audit provider connector configuration and lifecycle transitions.

Revision ID: 0019_connector_lifecycle_audit
Revises: 0018_connector_activation
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0019_connector_lifecycle_audit"
down_revision: Union[str, None] = "0018_connector_activation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LIFECYCLE_ACTIONS = (
    "'configure', 'credential_change', 'permission_change', 'authenticate', "
    "'discover', 'execute', 'health', 'activate', 'disconnect', 'reconnect', 'revoke'"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_connector_executions_action_allowed"),
        "connector_executions",
        f"action IN ({_LIFECYCLE_ACTIONS})",
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM connector_executions WHERE action IN "
        "('configure', 'credential_change', 'permission_change', 'authenticate', "
        "'activate', 'disconnect', 'reconnect', 'revoke')"
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
