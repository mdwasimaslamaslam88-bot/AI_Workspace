"""Admit audited chat filesystem tools.

Revision ID: 0023_chat_filesystem_tools
Revises: 0022_persistent_agent_missions
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_chat_filesystem_tools"
down_revision: Union[str, None] = "0022_persistent_agent_missions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TOOL_NAMES = (
    "calculator",
    "local_time",
    "document_search",
    "conversation_search",
    "memory_search",
    "filesystem.write",
    "filesystem.read",
    "filesystem.exists",
    "filesystem.list",
    "filesystem.stat",
)
_PERMISSIONS = (
    "utility",
    "personal_documents_read",
    "personal_conversations_read",
    "personal_memory_read",
    "workspace_read",
    "workspace_write",
)
_INITIATORS = ("explicit_user", "workflow", "chat_model", "chat_verifier")
_ERROR_CODES = (
    "tool_timed_out",
    "tool_cancelled",
    "tool_execution_failed",
    "tool_unavailable",
    "server_restarted",
    "tool_permission_denied",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace_constraints() -> None:
    for name in (
        "tool_name_allowed",
        "permission_allowed",
        "initiator_allowed",
        "error_code_allowed",
    ):
        op.drop_constraint(op.f(f"ck_tool_executions_{name}"), "tool_executions")
    op.create_check_constraint(
        op.f("ck_tool_executions_tool_name_allowed"),
        "tool_executions",
        f"tool_name IN ({_quoted(_TOOL_NAMES)})",
    )
    op.create_check_constraint(
        op.f("ck_tool_executions_permission_allowed"),
        "tool_executions",
        f"permission IN ({_quoted(_PERMISSIONS)})",
    )
    op.create_check_constraint(
        op.f("ck_tool_executions_initiator_allowed"),
        "tool_executions",
        f"initiator IN ({_quoted(_INITIATORS)})",
    )
    op.create_check_constraint(
        op.f("ck_tool_executions_error_code_allowed"),
        "tool_executions",
        "error_code IS NULL OR "
        f"error_code IN ({_quoted(_ERROR_CODES)})",
    )


def upgrade() -> None:
    _replace_constraints()


def downgrade() -> None:
    # Preserve immutable execution evidence during a rollback. The widened
    # constraints remain valid for the previous application, while a later
    # re-upgrade remains idempotent through the same replacement operation.
    _replace_constraints()
