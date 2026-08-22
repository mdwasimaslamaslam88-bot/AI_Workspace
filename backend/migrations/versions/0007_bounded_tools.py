"""Add bounded owner-scoped tool execution history.

Revision ID: 0007_bounded_tools
Revises: 0006_personal_memory
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_bounded_tools"
down_revision: Union[str, None] = "0006_personal_memory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "failed",
                "timed_out",
                "cancelled",
                name="tool_execution_status",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("initiator", sa.String(length=32), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "tool_name IN ('calculator', 'local_time', 'document_search', "
            "'conversation_search', 'memory_search')",
            name=op.f("ck_tool_executions_tool_name_allowed"),
        ),
        sa.CheckConstraint(
            "permission IN ('utility', 'personal_documents_read', "
            "'personal_conversations_read', 'personal_memory_read')",
            name=op.f("ck_tool_executions_permission_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'timed_out', 'cancelled')",
            name=op.f("ck_tool_executions_status_allowed"),
        ),
        sa.CheckConstraint(
            "initiator = 'explicit_user'",
            name=op.f("ck_tool_executions_initiator_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(arguments_json) BETWEEN 2 AND 8192",
            name=op.f("ck_tool_executions_arguments_json_bounded"),
        ),
        sa.CheckConstraint(
            "result_json IS NULL OR char_length(result_json) <= 16384",
            name=op.f("ck_tool_executions_result_json_bounded"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('tool_timed_out', "
            "'tool_cancelled', 'tool_execution_failed', 'tool_unavailable', "
            "'server_restarted')",
            name=op.f("ck_tool_executions_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL AND duration_ms >= 0) OR "
            "(status IN ('failed', 'timed_out', 'cancelled') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND duration_ms >= 0)",
            name=op.f("ck_tool_executions_terminal_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_tool_executions_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_tool_executions_conversation_id_conversations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_executions")),
    )
    op.create_index(
        "ix_tool_executions_owner_started_at",
        "tool_executions",
        ["owner_id", sa.literal_column("started_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_tool_executions_owner_conversation",
        "tool_executions",
        ["owner_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_executions_owner_conversation", table_name="tool_executions"
    )
    op.drop_index(
        "ix_tool_executions_owner_started_at", table_name="tool_executions"
    )
    op.drop_table("tool_executions")
