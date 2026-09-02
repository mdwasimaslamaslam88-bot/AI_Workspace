"""Add grounded owner-scoped marketing campaign workflows.

Revision ID: 0014_marketing_campaigns
Revises: 0013_owner_connectors
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_marketing_campaigns"
down_revision: Union[str, None] = "0013_owner_connectors"
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
        "marketing_campaigns",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("objective", sa.String(length=2000), nullable=False),
        sa.Column("product", sa.String(length=500), nullable=False),
        sa.Column("audience", sa.String(length=1000), nullable=False),
        sa.Column("channels_json", sa.String(length=256), nullable=False),
        sa.Column("source_facts_json", sa.Text(), nullable=False),
        sa.Column("publisher_connector_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("publish_path", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            _enum(
                "pending",
                "running",
                "needs_approval",
                "publishing",
                "awaiting_analytics",
                "completed",
                "failed",
                "cancelled",
                "timed_out",
                name="marketing_campaign_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "current_stage",
            _enum(
                "research",
                "strategy",
                "content",
                "creative",
                "approval",
                "publish",
                "analytics",
                "optimization",
                name="marketing_campaign_stage",
                length=24,
            ),
            nullable=True,
        ),
        sa.Column("analytics_json", sa.Text(), nullable=True),
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
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name=op.f("ck_marketing_campaigns_name_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(objective)) BETWEEN 1 AND 2000",
            name=op.f("ck_marketing_campaigns_objective_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(product)) BETWEEN 1 AND 500",
            name=op.f("ck_marketing_campaigns_product_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(audience)) BETWEEN 1 AND 1000",
            name=op.f("ck_marketing_campaigns_audience_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(channels_json) BETWEEN 2 AND 256",
            name=op.f("ck_marketing_campaigns_channels_json_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(source_facts_json) BETWEEN 2 AND 32768",
            name=op.f("ck_marketing_campaigns_source_facts_json_bounded"),
        ),
        sa.CheckConstraint(
            "analytics_json IS NULL OR char_length(analytics_json) <= 8192",
            name=op.f("ck_marketing_campaigns_analytics_json_bounded"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'needs_approval', 'publishing', "
            "'awaiting_analytics', 'completed', 'failed', 'cancelled', 'timed_out')",
            name=op.f("ck_marketing_campaigns_status_allowed"),
        ),
        sa.CheckConstraint(
            "current_stage IS NULL OR current_stage IN ('research', 'strategy', "
            "'content', 'creative', 'approval', 'publish', 'analytics', "
            "'optimization')",
            name=op.f("ck_marketing_campaigns_current_stage_allowed"),
        ),
        sa.CheckConstraint(
            "(publisher_connector_id IS NULL AND publish_path IS NULL) OR "
            "(publisher_connector_id IS NOT NULL AND "
            "char_length(publish_path) BETWEEN 1 AND 512)",
            name=op.f(
                "ck_marketing_campaigns_publisher_configuration_consistent"
            ),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('campaign_cancelled', "
            "'campaign_timed_out', 'agent_failed', 'verification_failed', "
            "'publisher_unavailable', 'publish_failed', 'server_restarted', "
            "'internal_failure')",
            name=op.f("ck_marketing_campaigns_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND current_stage IS NULL AND started_at IS NULL "
            "AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'running' AND current_stage IN ('research', 'strategy', "
            "'content', 'creative') AND started_at IS NOT NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'needs_approval' AND current_stage = 'approval' AND "
            "started_at IS NOT NULL AND approved_at IS NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'publishing' AND current_stage = 'publish' AND "
            "approved_at IS NOT NULL AND completed_at IS NULL AND "
            "error_code IS NULL) OR "
            "(status = 'awaiting_analytics' AND current_stage = 'analytics' AND "
            "approved_at IS NOT NULL AND published_at IS NOT NULL AND "
            "completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'completed' AND current_stage = 'optimization' AND "
            "approved_at IS NOT NULL AND published_at IS NOT NULL AND "
            "completed_at IS NOT NULL AND analytics_json IS NOT NULL AND "
            "error_code IS NULL) OR "
            "(status IN ('failed', 'cancelled', 'timed_out') AND "
            "completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name=op.f("ck_marketing_campaigns_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_marketing_campaigns_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publisher_connector_id", "owner_id"],
            ["connectors.id", "connectors.owner_id"],
            name="fk_marketing_campaigns_publisher_owner_connectors",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_campaigns")),
        sa.UniqueConstraint(
            "id", "owner_id", name="uq_marketing_campaigns_id_owner"
        ),
    )
    op.create_table(
        "marketing_stages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("campaign_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "research",
                "strategy",
                "content",
                "creative",
                "approval",
                "publish",
                "analytics",
                "optimization",
                name="marketing_stage_kind",
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "pending",
                "running",
                "blocked",
                "completed",
                "failed",
                "cancelled",
                name="marketing_stage_status",
            ),
            nullable=False,
        ),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_id", sa.String(length=96), nullable=True),
        sa.Column("connector_execution_id", sa.Uuid(as_uuid=True), nullable=True),
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
            name=op.f("ck_marketing_stages_position_bounded"),
        ),
        sa.CheckConstraint(
            "(position = 1 AND kind = 'research') OR "
            "(position = 2 AND kind = 'strategy') OR "
            "(position = 3 AND kind = 'content') OR "
            "(position = 4 AND kind = 'creative') OR "
            "(position = 5 AND kind = 'approval') OR "
            "(position = 6 AND kind = 'publish') OR "
            "(position = 7 AND kind = 'analytics') OR "
            "(position = 8 AND kind = 'optimization')",
            name=op.f("ck_marketing_stages_position_kind_consistent"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'blocked', 'completed', "
            "'failed', 'cancelled')",
            name=op.f("ck_marketing_stages_status_allowed"),
        ),
        sa.CheckConstraint(
            "output IS NULL OR char_length(output) <= 32768",
            name=op.f("ck_marketing_stages_output_bounded"),
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_marketing_stages_output_sha256_valid"),
        ),
        sa.CheckConstraint(
            "model_id IS NULL OR char_length(model_id) BETWEEN 1 AND 96",
            name=op.f("ck_marketing_stages_model_id_bounded"),
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ('campaign_cancelled', "
            "'campaign_timed_out', 'agent_failed', 'verification_failed', "
            "'publisher_unavailable', 'publish_failed', 'server_restarted', "
            "'not_run', 'internal_failure')",
            name=op.f("ck_marketing_stages_error_code_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND output IS NULL AND output_sha256 IS NULL AND error_code IS NULL "
            "AND duration_ms IS NULL) OR "
            "(status = 'blocked' AND kind = 'approval' AND started_at IS NULL "
            "AND completed_at IS NULL AND output IS NULL AND "
            "output_sha256 IS NULL AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND "
            "completed_at IS NULL AND output IS NULL AND output_sha256 IS NULL "
            "AND error_code IS NULL AND duration_ms IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL AND "
            "completed_at IS NOT NULL AND output IS NOT NULL AND "
            "output_sha256 IS NOT NULL AND error_code IS NULL AND "
            "duration_ms >= 0) OR "
            "(status IN ('failed', 'cancelled') AND completed_at IS NOT NULL "
            "AND output IS NULL AND output_sha256 IS NULL AND "
            "error_code IS NOT NULL AND duration_ms >= 0)",
            name=op.f("ck_marketing_stages_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_marketing_stages_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "owner_id"],
            ["marketing_campaigns.id", "marketing_campaigns.owner_id"],
            name="fk_marketing_stages_campaign_owner_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_execution_id"],
            ["connector_executions.id"],
            name=op.f(
                "fk_marketing_stages_connector_execution_id_connector_executions"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketing_stages")),
        sa.UniqueConstraint(
            "campaign_id",
            "position",
            name="uq_marketing_stages_position",
        ),
    )
    op.create_index(
        "ix_marketing_campaigns_owner_created_at",
        "marketing_campaigns",
        ["owner_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_marketing_campaigns_owner_status",
        "marketing_campaigns",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_marketing_stages_owner_campaign_position",
        "marketing_stages",
        ["owner_id", "campaign_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketing_stages_owner_campaign_position",
        table_name="marketing_stages",
    )
    op.drop_index(
        "ix_marketing_campaigns_owner_status",
        table_name="marketing_campaigns",
    )
    op.drop_index(
        "ix_marketing_campaigns_owner_created_at",
        table_name="marketing_campaigns",
    )
    op.drop_table("marketing_stages")
    op.drop_table("marketing_campaigns")
