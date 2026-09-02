from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


MAX_FINANCE_WORKSPACES_PER_OWNER = 10
MAX_FINANCE_ARTIFACTS_PER_WORKSPACE = 250
MAX_PAPER_ORDERS_PER_WORKSPACE = 1_000
MAX_WATCH_ITEMS_PER_WORKSPACE = 100
MAX_MARKET_ALERTS_PER_WORKSPACE = 100


class MarketAssetClass(StrEnum):
    INDIAN_STOCK = "indian_stock"
    GLOBAL_STOCK = "global_stock"
    CRYPTO = "crypto"
    FX = "fx"


class FinanceArtifactKind(StrEnum):
    RESEARCH = "research"
    STRATEGY = "strategy"
    BACKTEST = "backtest"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    JOURNAL = "journal"


class PaperOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PaperOrderStatus(StrEnum):
    EXECUTED = "executed"
    REJECTED = "rejected"


class MarketAlertCondition(StrEnum):
    AT_OR_ABOVE = "at_or_above"
    AT_OR_BELOW = "at_or_below"


class MarketAlertStatus(StrEnum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"


def _enum(enum_type, name: str, length: int = 32):
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda value: [member.value for member in value],
        length=length,
    )


class FinanceWorkspace(Base):
    __tablename__ = "finance_workspaces"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(name)) BETWEEN 1 AND 120",
            name="name_bounded_nonblank",
        ),
        CheckConstraint(
            "base_currency ~ '^[A-Z]{3}$'",
            name="base_currency_valid",
        ),
        CheckConstraint(
            "initial_cash_minor BETWEEN 1 AND 1000000000000000",
            name="initial_cash_bounded",
        ),
        CheckConstraint(
            "cash_minor BETWEEN 0 AND 1000000000000000",
            name="cash_bounded",
        ),
        CheckConstraint(
            "max_order_bps BETWEEN 1 AND 10000",
            name="max_order_bps_bounded",
        ),
        CheckConstraint(
            "max_position_bps BETWEEN 1 AND 10000",
            name="max_position_bps_bounded",
        ),
        CheckConstraint(
            "max_order_bps <= max_position_bps",
            name="risk_limits_consistent",
        ),
        UniqueConstraint("id", "owner_id", name="uq_finance_workspaces_id_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    initial_cash_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cash_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_order_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_position_bps: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    watch_items: Mapped[list[MarketWatchItem]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="MarketWatchItem.symbol"
    )
    positions: Mapped[list[PaperPosition]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PaperPosition.symbol"
    )
    orders: Mapped[list[PaperOrder]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PaperOrder.created_at.desc()",
    )
    alerts: Mapped[list[MarketAlert]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="MarketAlert.created_at.desc()",
    )
    artifacts: Mapped[list[FinanceArtifact]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="FinanceArtifact.created_at.desc()",
    )


class MarketWatchItem(Base):
    __tablename__ = "market_watch_items"
    __table_args__ = (
        CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name="symbol_valid",
        ),
        CheckConstraint(
            "char_length(trim(display_name)) BETWEEN 1 AND 120",
            name="display_name_bounded_nonblank",
        ),
        CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name="asset_class_allowed",
        ),
        UniqueConstraint(
            "workspace_id", "asset_class", "symbol", name="uq_market_watch_identity"
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("finance_workspaces.id", "finance_workspaces.owner_id"),
            name="fk_market_watch_workspace_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_class: Mapped[MarketAssetClass] = mapped_column(
        _enum(MarketAssetClass, "market_asset_class"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name="asset_class_allowed",
        ),
        CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name="symbol_valid",
        ),
        CheckConstraint(
            "quantity_micros BETWEEN 1 AND 1000000000000000",
            name="quantity_bounded",
        ),
        CheckConstraint(
            "cost_basis_minor BETWEEN 0 AND 1000000000000000",
            name="cost_basis_bounded",
        ),
        UniqueConstraint(
            "workspace_id", "asset_class", "symbol", name="uq_paper_position_identity"
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("finance_workspaces.id", "finance_workspaces.owner_id"),
            name="fk_paper_positions_workspace_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_class: Mapped[MarketAssetClass] = mapped_column(
        _enum(MarketAssetClass, "paper_position_asset_class"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_basis_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name="asset_class_allowed",
        ),
        CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name="symbol_valid",
        ),
        CheckConstraint("side IN ('buy', 'sell')", name="side_allowed"),
        CheckConstraint(
            "status IN ('executed', 'rejected')", name="status_allowed"
        ),
        CheckConstraint(
            "quantity_micros BETWEEN 1 AND 1000000000000000",
            name="quantity_bounded",
        ),
        CheckConstraint(
            "price_minor BETWEEN 1 AND 1000000000000000",
            name="price_bounded",
        ),
        CheckConstraint(
            "notional_minor BETWEEN 1 AND 1000000000000000",
            name="notional_bounded",
        ),
        CheckConstraint(
            "cash_after_minor BETWEEN 0 AND 1000000000000000",
            name="cash_after_bounded",
        ),
        CheckConstraint(
            "char_length(trim(source_reference)) BETWEEN 1 AND 512",
            name="source_reference_bounded_nonblank",
        ),
        CheckConstraint(
            "(status = 'executed' AND rejection_code IS NULL) OR "
            "(status = 'rejected' AND rejection_code IN "
            "('owner_confirmation_required', 'order_limit', 'position_limit', "
            "'insufficient_cash', 'insufficient_position'))",
            name="outcome_consistent",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("finance_workspaces.id", "finance_workspaces.owner_id"),
            name="fk_paper_orders_workspace_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_class: Mapped[MarketAssetClass] = mapped_column(
        _enum(MarketAssetClass, "paper_order_asset_class"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    side: Mapped[PaperOrderSide] = mapped_column(
        _enum(PaperOrderSide, "paper_order_side", length=8), nullable=False
    )
    quantity_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    notional_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PaperOrderStatus] = mapped_column(
        _enum(PaperOrderStatus, "paper_order_status"), nullable=False
    )
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    cash_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MarketAlert(Base):
    __tablename__ = "market_alerts"
    __table_args__ = (
        CheckConstraint(
            "asset_class IN ('indian_stock', 'global_stock', 'crypto', 'fx')",
            name="asset_class_allowed",
        ),
        CheckConstraint(
            "symbol ~ '^[A-Z0-9][A-Z0-9._:/-]{0,23}$'",
            name="symbol_valid",
        ),
        CheckConstraint(
            "condition IN ('at_or_above', 'at_or_below')",
            name="condition_allowed",
        ),
        CheckConstraint(
            "status IN ('active', 'triggered', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "threshold_minor BETWEEN 1 AND 1000000000000000",
            name="threshold_bounded",
        ),
        CheckConstraint(
            "last_price_minor IS NULL OR last_price_minor BETWEEN 1 AND 1000000000000000",
            name="last_price_bounded",
        ),
        CheckConstraint(
            "last_source_reference IS NULL OR "
            "char_length(trim(last_source_reference)) BETWEEN 1 AND 512",
            name="last_source_reference_bounded",
        ),
        CheckConstraint(
            "(status = 'active' AND triggered_at IS NULL) OR "
            "(status = 'triggered' AND triggered_at IS NOT NULL) OR "
            "status = 'cancelled'",
            name="lifecycle_consistent",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("finance_workspaces.id", "finance_workspaces.owner_id"),
            name="fk_market_alerts_workspace_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    asset_class: Mapped[MarketAssetClass] = mapped_column(
        _enum(MarketAssetClass, "market_alert_asset_class"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(24), nullable=False)
    condition: Mapped[MarketAlertCondition] = mapped_column(
        _enum(MarketAlertCondition, "market_alert_condition"), nullable=False
    )
    threshold_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[MarketAlertStatus] = mapped_column(
        _enum(MarketAlertStatus, "market_alert_status"), nullable=False
    )
    last_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    last_source_reference: Mapped[str | None] = mapped_column(String(512))
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinanceArtifact(Base):
    __tablename__ = "finance_artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('research', 'strategy', 'backtest', 'portfolio', 'risk', 'journal')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "char_length(trim(title)) BETWEEN 1 AND 160",
            name="title_bounded_nonblank",
        ),
        CheckConstraint(
            "char_length(trim(source_reference)) BETWEEN 1 AND 512",
            name="source_reference_bounded_nonblank",
        ),
        CheckConstraint(
            "input_sha256 ~ '^[0-9a-f]{64}$'",
            name="input_sha256_valid",
        ),
        CheckConstraint(
            "char_length(output) BETWEEN 1 AND 65536",
            name="output_bounded",
        ),
        CheckConstraint(
            "output_sha256 ~ '^[0-9a-f]{64}$'",
            name="output_sha256_valid",
        ),
        CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 96",
            name="model_id_bounded",
        ),
        CheckConstraint(
            "duration_ms BETWEEN 0 AND 2147483647",
            name="duration_ms_bounded",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "owner_id"),
            ("finance_workspaces.id", "finance_workspaces.owner_id"),
            name="fk_finance_artifacts_workspace_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[FinanceArtifactKind] = mapped_column(
        _enum(FinanceArtifactKind, "finance_artifact_kind"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(512), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[str] = mapped_column(Text, nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "ix_finance_workspaces_owner_created_at",
    FinanceWorkspace.owner_id,
    FinanceWorkspace.created_at.desc(),
)
Index(
    "ix_paper_orders_workspace_created_at",
    PaperOrder.workspace_id,
    PaperOrder.created_at.desc(),
)
Index(
    "ix_market_alerts_workspace_status",
    MarketAlert.workspace_id,
    MarketAlert.status,
)
Index(
    "ix_finance_artifacts_workspace_created_at",
    FinanceArtifact.workspace_id,
    FinanceArtifact.created_at.desc(),
)
