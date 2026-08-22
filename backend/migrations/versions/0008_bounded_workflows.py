"""Add bounded owner-scoped workflow execution.

Revision ID: 0008_bounded_workflows
Revises: 0007_bounded_tools
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_bounded_workflows"
down_revision: Union[str, None] = "0007_bounded_tools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _status_enum(name: str) -> sa.Enum:
    return sa.Enum(
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        name=name,
        native_enum=False,
        create_constraint=False,
        length=32,
    )


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_tool_executions_initiator_allowed"),
        "tool_executions",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_tool_executions_initiator_allowed"),
        "tool_executions",
        "initiator IN ('explicit_user', 'workflow')",
    )
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("status", _status_enum("workflow_status"), nullable=False),
        sa.Column("step_count", sa.SmallInteger(), nullable=False),
        sa.Column("current_step_position", sa.SmallInteger(), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "name IS NULL OR (char_length(name) BETWEEN 1 AND 120 "
            "AND char_length(btrim(name)) > 0)",
            name=op.f("ck_workflows_name_bounded_non_blank"),
        ),
        sa.CheckConstraint(
            "step_count BETWEEN 1 AND 8",
            name=op.f("ck_workflows_step_count_bounded"),
        ),
        sa.CheckConstraint(
            "current_step_position IS NULL OR current_step_position "
            "BETWEEN 1 AND step_count",
            name=op.f("ck_workflows_current_step_position_bounded"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name=op.f("ck_workflows_status_allowed"),
        ),
        sa.CheckConstraint(
            "result_json IS NULL OR char_length(result_json) <= 65536",
            name=op.f("ck_workflows_result_json_bounded"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('workflow_cancelled', "
            "'workflow_timed_out', 'step_failed', 'output_too_large', "
            "'server_restarted', 'internal_failure')",
            name=op.f("ck_workflows_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND current_step_position IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND cancel_requested = false) OR "
            "(status = 'running' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND current_step_position IS NOT NULL "
            "AND result_json IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND current_step_position IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL "
            "AND cancel_requested = false) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL)",
            name=op.f("ck_workflows_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_workflows_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            name=op.f("uq_workflows_id_owner"),
        ),
    )
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.Column("arguments_json", sa.Text(), nullable=False),
        sa.Column(
            "status", _status_enum("workflow_step_status"), nullable=False
        ),
        sa.Column("tool_execution_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 8",
            name=op.f("ck_workflow_steps_position_bounded"),
        ),
        sa.CheckConstraint(
            "tool_name IN ('calculator', 'local_time', 'document_search', "
            "'conversation_search', 'memory_search')",
            name=op.f("ck_workflow_steps_tool_name_allowed"),
        ),
        sa.CheckConstraint(
            "permission IN ('utility', 'personal_documents_read', "
            "'personal_conversations_read', 'personal_memory_read')",
            name=op.f("ck_workflow_steps_permission_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', "
            "'cancelled', 'timed_out')",
            name=op.f("ck_workflow_steps_status_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(arguments_json) BETWEEN 2 AND 8192",
            name=op.f("ck_workflow_steps_arguments_json_bounded"),
        ),
        sa.CheckConstraint(
            "result_json IS NULL OR char_length(result_json) <= 16384",
            name=op.f("ck_workflow_steps_result_json_bounded"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('tool_timed_out', "
            "'tool_cancelled', 'tool_execution_failed', 'tool_unavailable', "
            "'step_timed_out', 'workflow_cancelled', 'server_restarted', "
            "'not_run', 'internal_failure')",
            name=op.f("ck_workflow_steps_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND tool_execution_id IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND result_json IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND tool_execution_id IS NOT NULL "
            "AND result_json IS NOT NULL AND error_code IS NULL "
            "AND duration_ms >= 0) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') "
            "AND completed_at IS NOT NULL AND result_json IS NULL "
            "AND error_code IS NOT NULL AND duration_ms >= 0)",
            name=op.f("ck_workflow_steps_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_workflow_steps_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tool_execution_id"],
            ["tool_executions.id"],
            name=op.f("fk_workflow_steps_tool_execution_id_tool_executions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id", "owner_id"],
            ["workflows.id", "workflows.owner_id"],
            name=op.f("fk_workflow_steps_workflow_owner_workflows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_steps")),
        sa.UniqueConstraint(
            "workflow_id",
            "position",
            name=op.f("uq_workflow_steps_position"),
        ),
    )
    op.create_index(
        "ix_workflows_owner_created_at",
        "workflows",
        ["owner_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_workflows_owner_status",
        "workflows",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_workflow_steps_owner_workflow_position",
        "workflow_steps",
        ["owner_id", "workflow_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_steps_owner_workflow_position",
        table_name="workflow_steps",
    )
    op.drop_index("ix_workflows_owner_status", table_name="workflows")
    op.drop_index("ix_workflows_owner_created_at", table_name="workflows")
    op.drop_table("workflow_steps")
    op.drop_table("workflows")
    op.drop_constraint(
        op.f("ck_tool_executions_initiator_allowed"),
        "tool_executions",
        type_="check",
    )
    op.execute(
        sa.text(
            "UPDATE tool_executions SET initiator = 'explicit_user' "
            "WHERE initiator = 'workflow'"
        )
    )
    op.create_check_constraint(
        op.f("ck_tool_executions_initiator_allowed"),
        "tool_executions",
        "initiator = 'explicit_user'",
    )
