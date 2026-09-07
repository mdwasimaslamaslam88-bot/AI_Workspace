"""Admit audited DEX agent delegation.

Revision ID: 0024_dex_delegation_tool
Revises: 0023_chat_filesystem_tools
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0024_dex_delegation_tool"
down_revision: Union[str, None] = "0023_chat_filesystem_tools"
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
    "dex.delegate",
)
_PERMISSIONS = (
    "utility",
    "personal_documents_read",
    "personal_conversations_read",
    "personal_memory_read",
    "workspace_read",
    "workspace_write",
    "agent_delegation",
)
_INITIATORS = (
    "explicit_user",
    "workflow",
    "chat_model",
    "chat_verifier",
    "dex_agent",
)
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
        "error_code IS NULL OR " f"error_code IN ({_quoted(_ERROR_CODES)})",
    )


def upgrade() -> None:
    _replace_constraints()


def downgrade() -> None:
    # Preserve immutable delegation evidence during rollback. The prior
    # application ignores unknown historical tool rows safely.
    _replace_constraints()
