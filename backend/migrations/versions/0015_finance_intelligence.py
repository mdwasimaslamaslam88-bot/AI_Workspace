"""Add grounded finance intelligence and paper market operations.

Revision ID: 0015_finance_intelligence
Revises: 0014_marketing_campaigns
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_finance_intelligence"
down_revision: Union[str, None] = "0014_marketing_campaigns"
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
        "finance_workspaces",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("initial_cash_minor", sa.BigInteger(), nullable=False),
        sa.Column("cash_minor", sa.BigInteger(), nullable=False),
        sa.Column("max_order_bps", sa.SmallInteger(), nullable=False),
        sa.Column("max_position_bps", sa.SmallInteger(), nullable=False),
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
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name=op.f("ck_finance_workspaces_name_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$'",
            name=op.f("ck_finance_workspaces_base_currency_valid"),
        ),
        sa.CheckConstraint(
            "initial_cash_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_finance_workspaces_initial_cash_bounded"),
        ),
        sa.CheckConstraint(
            "cash_minor BETWEEN 0 AND 1000000000000000",
            name=op.f("ck_finance_workspaces_cash_bounded"),
        ),
        sa.CheckConstraint(
            "max_order_bps BETWEEN 1 AND 10000",
            name=op.f("ck_finance_workspaces_max_order_bps_bounded"),
        ),
        sa.CheckConstraint(
            "max_position_bps BETWEEN 1 AND 10000",
            name=op.f("ck_finance_workspaces_max_position_bps_bounded"),
        ),
        sa.CheckConstraint(
            "max_order_bps <= max_position_bps",
            name=op.f("ck_finance_workspaces_risk_limits_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_finance_workspaces_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_workspaces")),
        sa.UniqueConstraint(
            "id", "owner_id", name="uq_finance_workspaces_id_owner"
        ),
    )
    op.create_table(
        "market_watch_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "asset_class",
            _enum(
                "indian_stock",
                "global_stock",
                "crypto",
                "fx",
                name="market_asset_class",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name=op.f("ck_market_watch_items_symbol_valid"),
        ),
        sa.CheckConstraint(
            "char_length(trim(display_name)) BETWEEN 1 AND 120",
            name=op.f("ck_market_watch_items_display_name_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name=op.f("ck_market_watch_items_asset_class_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_market_watch_items_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_market_watch_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_watch_items")),
        sa.UniqueConstraint(
            "workspace_id",
            "asset_class",
            "symbol",
            name="uq_market_watch_identity",
        ),
    )
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "asset_class",
            _enum(
                "indian_stock",
                "global_stock",
                "crypto",
                "fx",
                name="paper_position_asset_class",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("quantity_micros", sa.BigInteger(), nullable=False),
        sa.Column("cost_basis_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name=op.f("ck_paper_positions_asset_class_allowed"),
        ),
        sa.CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name=op.f("ck_paper_positions_symbol_valid"),
        ),
        sa.CheckConstraint(
            "quantity_micros BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_paper_positions_quantity_bounded"),
        ),
        sa.CheckConstraint(
            "cost_basis_minor BETWEEN 0 AND 1000000000000000",
            name=op.f("ck_paper_positions_cost_basis_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_paper_positions_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_paper_positions_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_positions")),
        sa.UniqueConstraint(
            "workspace_id",
            "asset_class",
            "symbol",
            name="uq_paper_position_identity",
        ),
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "asset_class",
            _enum(
                "indian_stock",
                "global_stock",
                "crypto",
                "fx",
                name="paper_order_asset_class",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column(
            "side",
            _enum("buy", "sell", name="paper_order_side", length=8),
            nullable=False,
        ),
        sa.Column("quantity_micros", sa.BigInteger(), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("notional_minor", sa.BigInteger(), nullable=False),
        sa.Column("source_reference", sa.String(length=512), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            _enum("executed", "rejected", name="paper_order_status"),
            nullable=False,
        ),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.Column("cash_after_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name=op.f("ck_paper_orders_asset_class_allowed"),
        ),
        sa.CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name=op.f("ck_paper_orders_symbol_valid"),
        ),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')", name=op.f("ck_paper_orders_side_allowed")
        ),
        sa.CheckConstraint(
            "status IN ('executed', 'rejected')",
            name=op.f("ck_paper_orders_status_allowed"),
        ),
        sa.CheckConstraint(
            "quantity_micros BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_paper_orders_quantity_bounded"),
        ),
        sa.CheckConstraint(
            "price_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_paper_orders_price_bounded"),
        ),
        sa.CheckConstraint(
            "notional_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_paper_orders_notional_bounded"),
        ),
        sa.CheckConstraint(
            "cash_after_minor BETWEEN 0 AND 1000000000000000",
            name=op.f("ck_paper_orders_cash_after_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(trim(source_reference)) BETWEEN 1 AND 512",
            name=op.f("ck_paper_orders_source_reference_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "(status = 'executed' AND rejection_code IS NULL) OR "
            "(status = 'rejected' AND rejection_code IN "
            "('owner_confirmation_required', 'order_limit', 'position_limit', "
            "'insufficient_cash', 'insufficient_position'))",
            name=op.f("ck_paper_orders_outcome_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_paper_orders_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_paper_orders_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_orders")),
    )
    op.create_table(
        "market_alerts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "asset_class",
            _enum(
                "indian_stock",
                "global_stock",
                "crypto",
                "fx",
                name="market_alert_asset_class",
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column(
            "condition",
            _enum(
                "at_or_above",
                "at_or_below",
                name="market_alert_condition",
            ),
            nullable=False,
        ),
        sa.Column("threshold_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "active",
                "triggered",
                "cancelled",
                name="market_alert_status",
            ),
            nullable=False,
        ),
        sa.Column("last_price_minor", sa.BigInteger(), nullable=True),
        sa.Column("last_source_reference", sa.String(length=512), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name=op.f("ck_market_alerts_asset_class_allowed"),
        ),
        sa.CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name=op.f("ck_market_alerts_symbol_valid"),
        ),
        sa.CheckConstraint(
            "condition IN ('at_or_above', 'at_or_below')",
            name=op.f("ck_market_alerts_condition_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'triggered', 'cancelled')",
            name=op.f("ck_market_alerts_status_allowed"),
        ),
        sa.CheckConstraint(
            "threshold_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_market_alerts_threshold_bounded"),
        ),
        sa.CheckConstraint(
            "last_price_minor IS NULL OR last_price_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_market_alerts_last_price_bounded"),
        ),
        sa.CheckConstraint(
            "last_source_reference IS NULL OR "
            "char_length(trim(last_source_reference)) BETWEEN 1 AND 512",
            name=op.f("ck_market_alerts_last_source_reference_bounded"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND triggered_at IS NULL) OR "
            "(status = 'triggered' AND triggered_at IS NOT NULL) OR "
            "status = 'cancelled'",
            name=op.f("ck_market_alerts_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_market_alerts_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_market_alerts_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_alerts")),
    )
    op.create_table(
        "finance_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "research",
                "strategy",
                "backtest",
                "portfolio",
                "risk",
                "journal",
                name="finance_artifact_kind",
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("source_reference", sa.String(length=512), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("output_sha256", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=96), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('research', 'strategy', 'backtest', 'portfolio', 'risk', 'journal')",
            name=op.f("ck_finance_artifacts_kind_allowed"),
        ),
        sa.CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name=op.f("ck_finance_artifacts_title_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "char_length(trim(source_reference)) BETWEEN 1 AND 512",
            name=op.f("ck_finance_artifacts_source_reference_bounded_nonblank"),
        ),
        sa.CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_finance_artifacts_input_sha256_valid"),
        ),
        sa.CheckConstraint(
            "char_length(output) BETWEEN 1 AND 65536",
            name=op.f("ck_finance_artifacts_output_bounded"),
        ),
        sa.CheckConstraint(
            "output_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_finance_artifacts_output_sha256_valid"),
        ),
        sa.CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 96",
            name=op.f("ck_finance_artifacts_model_id_bounded"),
        ),
        sa.CheckConstraint(
            "duration_ms BETWEEN 0 AND 2147483647",
            name=op.f("ck_finance_artifacts_duration_ms_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name=op.f("fk_finance_artifacts_owner_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_finance_artifacts_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finance_artifacts")),
    )
    op.create_index(
        "ix_finance_workspaces_owner_created_at",
        "finance_workspaces",
        ["owner_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_paper_orders_workspace_created_at",
        "paper_orders",
        ["workspace_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_market_alerts_workspace_status",
        "market_alerts",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_finance_artifacts_workspace_created_at",
        "finance_artifacts",
        ["workspace_id", sa.literal_column("created_at").desc()],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_finance_artifacts_workspace_created_at", table_name="finance_artifacts"
    )
    op.drop_index("ix_market_alerts_workspace_status", table_name="market_alerts")
    op.drop_index(
        "ix_paper_orders_workspace_created_at", table_name="paper_orders"
    )
    op.drop_index(
        "ix_finance_workspaces_owner_created_at", table_name="finance_workspaces"
    )
    op.drop_table("finance_artifacts")
    op.drop_table("market_alerts")
    op.drop_table("paper_orders")
    op.drop_table("paper_positions")
    op.drop_table("market_watch_items")
    op.drop_table("finance_workspaces")
