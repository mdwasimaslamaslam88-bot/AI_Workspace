"""Add persistent owner-controlled Agent OS missions.

Revision ID: 0022_persistent_agent_missions
Revises: 0021_learning_knowledge_os
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_persistent_agent_missions"
down_revision: Union[str, None] = "0021_learning_knowledge_os"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_STATUSES = (
    "queued",
    "needs_approval",
    "planning",
    "running",
    "paused",
    "verifying",
    "retrying",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
)


def upgrade() -> None:
    status_list = ", ".join(f"'{value}'" for value in _STATUSES)
    op.create_table(
        "agent_missions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
        sa.Column(
            "pause_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "approved",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("revision", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("manual_retry_count", sa.SmallInteger(), server_default="0", nullable=False),
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
        sa.CheckConstraint(
            f"status IN ({status_list})",
            name=op.f("ck_agent_missions_status_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(request_json) BETWEEN 2 AND 65536",
            name=op.f("ck_agent_missions_request_json_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(record_json) BETWEEN 2 AND 1048576",
            name=op.f("ck_agent_missions_record_json_bounded"),
        ),
        sa.CheckConstraint(
            "revision BETWEEN 1 AND 16",
            name=op.f("ck_agent_missions_revision_bounded"),
        ),
        sa.CheckConstraint(
            "manual_retry_count BETWEEN 0 AND 3",
            name=op.f("ck_agent_missions_manual_retry_count_bounded"),
        ),
        sa.CheckConstraint(
            "NOT approved OR requires_approval",
            name=op.f("ck_agent_missions_approval_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_agent_missions_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_missions")),
        sa.UniqueConstraint("id", "owner_id", name="uq_agent_missions_id_owner"),
    )
    op.create_index(
        "ix_agent_missions_owner_created",
        "agent_missions",
        ["owner_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_agent_missions_status",
        "agent_missions",
        ["status"],
        unique=False,
    )

    op.create_table(
        "agent_mission_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mission_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.SmallInteger(), nullable=True),
        sa.Column("agent", sa.String(length=32), nullable=True),
        sa.Column("model_id", sa.String(length=96), nullable=True),
        sa.Column("detail_sha256", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 10000",
            name=op.f("ck_agent_mission_events_sequence_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(action) BETWEEN 1 AND 32",
            name=op.f("ck_agent_mission_events_action_bounded_non_blank"),
        ),
        sa.CheckConstraint(
            f"status IN ({status_list})",
            name=op.f("ck_agent_mission_events_status_allowed"),
        ),
        sa.CheckConstraint(
            "detail_sha256 IS NULL OR detail_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_mission_events_detail_sha256_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_agent_mission_events_owner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["mission_id", "owner_id"],
            ["agent_missions.id", "agent_missions.owner_id"],
            name="fk_agent_mission_events_mission_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_mission_events")),
        sa.UniqueConstraint("mission_id", "sequence", name="uq_agent_mission_events_sequence"),
    )
    op.create_index(
        "ix_agent_mission_events_owner_mission_sequence",
        "agent_mission_events",
        ["owner_id", "mission_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_mission_events_owner_mission_sequence",
        table_name="agent_mission_events",
    )
    op.drop_table("agent_mission_events")
    op.drop_index("ix_agent_missions_status", table_name="agent_missions")
    op.drop_index("ix_agent_missions_owner_created", table_name="agent_missions")
    op.drop_table("agent_missions")
