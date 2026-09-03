"""Add owner trading safety policy and verified broker order evidence.

Revision ID: 0020_trading_safety
Revises: 0019_connector_lifecycle_audit
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020_trading_safety"
down_revision: Union[str, None] = "0019_connector_lifecycle_audit"
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
        "trading_safety_policies",
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "execution_mode",
            _enum("paper", "live", name="trading_execution_mode"),
            nullable=False,
        ),
        sa.Column("broker_connector_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("broker_account_sha256", sa.String(length=64), nullable=True),
        sa.Column("account_path", sa.String(length=512), nullable=True),
        sa.Column("order_path", sa.String(length=512), nullable=True),
        sa.Column("order_status_prefix", sa.String(length=512), nullable=True),
        sa.Column("live_trading_enabled", sa.Boolean(), nullable=False),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False),
        sa.Column("owner_authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_order_value_minor", sa.BigInteger(), nullable=False),
        sa.Column("max_position_value_minor", sa.BigInteger(), nullable=False),
        sa.Column("daily_loss_limit_minor", sa.BigInteger(), nullable=False),
        sa.Column("per_symbol_exposure_limit_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_exposure_limit_minor", sa.BigInteger(), nullable=False),
        sa.Column("max_open_orders", sa.SmallInteger(), nullable=False),
        sa.Column("allowed_instruments_json", sa.Text(), nullable=False),
        sa.Column("allowed_venues_json", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "execution_mode IN ('paper', 'live')",
            name=op.f("ck_trading_safety_policies_execution_mode_allowed"),
        ),
        sa.CheckConstraint(
            "max_order_value_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_trading_safety_policies_max_order_value_bounded"),
        ),
        sa.CheckConstraint(
            "max_position_value_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_trading_safety_policies_max_position_value_bounded"),
        ),
        sa.CheckConstraint(
            "daily_loss_limit_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_trading_safety_policies_daily_loss_limit_bounded"),
        ),
        sa.CheckConstraint(
            "per_symbol_exposure_limit_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_trading_safety_policies_per_symbol_exposure_limit_bounded"),
        ),
        sa.CheckConstraint(
            "total_exposure_limit_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_trading_safety_policies_total_exposure_limit_bounded"),
        ),
        sa.CheckConstraint(
            "max_open_orders BETWEEN 1 AND 1000",
            name=op.f("ck_trading_safety_policies_max_open_orders_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(allowed_instruments_json) BETWEEN 2 AND 4096",
            name=op.f("ck_trading_safety_policies_allowed_instruments_json_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(allowed_venues_json) BETWEEN 2 AND 4096",
            name=op.f("ck_trading_safety_policies_allowed_venues_json_bounded"),
        ),
        sa.CheckConstraint(
            "broker_account_sha256 IS NULL OR broker_account_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_trading_safety_policies_broker_account_sha256_valid"),
        ),
        sa.CheckConstraint(
            "(execution_mode = 'paper') OR "
            "(account_path IS NOT NULL AND order_path IS NOT NULL AND "
            "order_status_prefix IS NOT NULL)",
            name=op.f("ck_trading_safety_policies_live_paths_configured"),
        ),
        sa.CheckConstraint(
            "(execution_mode = 'paper' AND live_trading_enabled = false) OR "
            "(execution_mode = 'live' AND broker_connector_id IS NOT NULL AND "
            "broker_account_sha256 IS NOT NULL AND owner_authorized_at IS NOT NULL AND "
            "session_valid_until IS NOT NULL)",
            name=op.f("ck_trading_safety_policies_live_configuration_complete"),
        ),
        sa.CheckConstraint(
            "live_trading_enabled = false OR "
            "(execution_mode = 'live' AND kill_switch_active = false)",
            name=op.f("ck_trading_safety_policies_live_enablement_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_trading_policy_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["broker_connector_id", "owner_id"],
            ["connectors.id", "connectors.owner_id"],
            name="fk_trading_policy_connector_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("workspace_id", name=op.f("pk_trading_safety_policies")),
    )
    op.create_table(
        "broker_order_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("broker_connector_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "asset_class",
            _enum(
                "indian_stock", "global_stock", "crypto", "fx", name="broker_order_asset_class"
            ),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=24), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "side", _enum("buy", "sell", name="broker_order_side", length=8), nullable=False
        ),
        sa.Column("quantity_micros", sa.BigInteger(), nullable=False),
        sa.Column("limit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("notional_minor", sa.BigInteger(), nullable=False),
        sa.Column("filled_quantity_micros", sa.BigInteger(), nullable=False),
        sa.Column("client_order_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("provider_order_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            _enum(
                "submitted",
                "verified_open",
                "verified_filled",
                "verified_cancelled",
                name="broker_order_status",
            ),
            nullable=False,
        ),
        sa.Column("submit_execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status_execution_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name=op.f("ck_broker_order_records_asset_class_allowed"),
        ),
        sa.CheckConstraint(
            "side IN ('buy', 'sell')", name=op.f("ck_broker_order_records_side_allowed")
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'verified_open', 'verified_filled', 'verified_cancelled')",
            name=op.f("ck_broker_order_records_status_allowed"),
        ),
        sa.CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name=op.f("ck_broker_order_records_symbol_valid"),
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name=op.f("ck_broker_order_records_currency_valid")
        ),
        sa.CheckConstraint(
            "quantity_micros BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_broker_order_records_quantity_bounded"),
        ),
        sa.CheckConstraint(
            "limit_price_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_broker_order_records_price_bounded"),
        ),
        sa.CheckConstraint(
            "notional_minor BETWEEN 1 AND 1000000000000000",
            name=op.f("ck_broker_order_records_notional_bounded"),
        ),
        sa.CheckConstraint(
            "filled_quantity_micros BETWEEN 0 AND quantity_micros",
            name=op.f("ck_broker_order_records_filled_quantity_bounded"),
        ),
        sa.CheckConstraint(
            "status != 'verified_filled' OR filled_quantity_micros = quantity_micros",
            name=op.f("ck_broker_order_records_filled_status_consistent"),
        ),
        sa.CheckConstraint(
            "client_order_key_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_broker_order_records_client_key_sha256_valid"),
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_broker_order_records_request_sha256_valid"),
        ),
        sa.CheckConstraint(
            "provider_order_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_broker_order_records_provider_order_sha256_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_broker_order_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["broker_connector_id", "owner_id"],
            ["connectors.id", "connectors.owner_id"],
            name="fk_broker_order_connector_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submit_execution_id"],
            ["connector_executions.id"],
            name="fk_broker_order_submit_execution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["status_execution_id"],
            ["connector_executions.id"],
            name="fk_broker_order_status_execution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broker_order_records")),
        sa.UniqueConstraint(
            "workspace_id",
            "client_order_key_sha256",
            name="uq_broker_order_idempotency",
        ),
    )
    op.create_table(
        "trading_safety_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "action",
            _enum(
                "configured",
                "live_enabled",
                "live_disabled",
                "kill_switch_activated",
                name="trading_safety_action",
            ),
            nullable=False,
        ),
        sa.Column("policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("connector_execution_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('configured', 'live_enabled', 'live_disabled', 'kill_switch_activated')",
            name=op.f("ck_trading_safety_events_action_allowed"),
        ),
        sa.CheckConstraint(
            "policy_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_trading_safety_events_policy_sha256_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "owner_id"],
            ["finance_workspaces.id", "finance_workspaces.owner_id"],
            name="fk_trading_safety_event_workspace_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_execution_id"],
            ["connector_executions.id"],
            name="fk_trading_safety_event_connector_execution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_safety_events")),
    )
    op.create_index(
        "ix_trading_safety_events_workspace_created_at",
        "trading_safety_events",
        ["workspace_id", sa.literal_column("created_at").desc()],
        unique=False,
    )
    op.create_index(
        "ix_broker_order_records_workspace_created_at",
        "broker_order_records",
        ["workspace_id", sa.literal_column("created_at").desc()],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broker_order_records_workspace_created_at", table_name="broker_order_records"
    )
    op.drop_index(
        "ix_trading_safety_events_workspace_created_at", table_name="trading_safety_events"
    )
    op.drop_table("trading_safety_events")
    op.drop_table("broker_order_records")
    op.drop_table("trading_safety_policies")
