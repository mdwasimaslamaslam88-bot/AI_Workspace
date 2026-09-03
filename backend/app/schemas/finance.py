from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.finance import (
    BrokerOrderStatus,
    FinanceArtifactKind,
    MarketAlertCondition,
    MarketAlertStatus,
    MarketAssetClass,
    PaperOrderSide,
    PaperOrderStatus,
    TradingExecutionMode,
    TradingSafetyAction,
)


_SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9._:/-]{0,23}$"
_TEXT_PATTERN = r"^\S(?:[\s\S]*\S)?$"


class FinanceWorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=_TEXT_PATTERN)
    base_currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    initial_cash_minor: int = Field(strict=True, ge=1, le=10**15)
    max_order_bps: int = Field(default=1_000, strict=True, ge=1, le=10_000)
    max_position_bps: int = Field(default=2_500, strict=True, ge=1, le=10_000)

    @model_validator(mode="after")
    def risk_limits_are_consistent(self):
        if self.max_order_bps > self.max_position_bps:
            raise ValueError("max order risk cannot exceed max position risk")
        return self


class WatchItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    display_name: str = Field(min_length=1, max_length=120, pattern=_TEXT_PATTERN)


class MarketSourceFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)
    fact: str = Field(min_length=1, max_length=2_000, pattern=_TEXT_PATTERN)


class MarketResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[FinanceArtifactKind.RESEARCH, FinanceArtifactKind.STRATEGY]
    asset_class: MarketAssetClass
    subject: str = Field(min_length=1, max_length=500, pattern=_TEXT_PATTERN)
    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)
    source_facts: list[MarketSourceFactRequest] = Field(min_length=1, max_length=16)

    @field_validator("source_facts")
    @classmethod
    def source_facts_are_unique(
        cls, values: list[MarketSourceFactRequest]
    ) -> list[MarketSourceFactRequest]:
        identities = {(value.source_reference, value.fact) for value in values}
        if len(identities) != len(values):
            raise ValueError("market source facts must be unique")
        return values


class MarketBarRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    close_minor: int = Field(strict=True, ge=1, le=10**15)

    @field_validator("observed_at")
    @classmethod
    def time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("market observation requires a timezone")
        return value


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)
    bars: list[MarketBarRequest] = Field(min_length=3, max_length=512)
    fast_window: int = Field(strict=True, ge=2, le=199)
    slow_window: int = Field(strict=True, ge=3, le=200)
    initial_cash_minor: int = Field(strict=True, ge=1, le=10**15)
    fee_bps: int = Field(default=0, strict=True, ge=0, le=1_000)
    slippage_bps: int = Field(default=0, strict=True, ge=0, le=1_000)
    position_size_bps: int = Field(default=10_000, strict=True, ge=1, le=10_000)
    stop_loss_bps: int | None = Field(default=None, strict=True, ge=1, le=10_000)
    take_profit_bps: int | None = Field(default=None, strict=True, ge=1, le=100_000)

    @model_validator(mode="after")
    def windows_and_bars_are_valid(self):
        if self.fast_window >= self.slow_window or len(self.bars) < self.slow_window:
            raise ValueError("backtest windows are invalid")
        if any(
            right.observed_at <= left.observed_at
            for left, right in zip(self.bars, self.bars[1:], strict=False)
        ):
            raise ValueError("backtest bars must be strictly ordered")
        return self


class PaperOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: Literal["paper"]
    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    side: PaperOrderSide
    quantity_micros: int = Field(strict=True, ge=1, le=10**15)
    price_minor: int = Field(strict=True, ge=1, le=10**15)
    observed_at: datetime
    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)
    owner_confirmed: bool = Field(strict=True)

    @field_validator("observed_at")
    @classmethod
    def order_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("paper quote requires a timezone")
        return value


class MarketQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    price_minor: int = Field(strict=True, ge=1, le=10**15)
    observed_at: datetime
    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)

    @field_validator("observed_at")
    @classmethod
    def quote_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("portfolio quote requires a timezone")
        return value


class PortfolioAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)
    quotes: list[MarketQuoteRequest] = Field(max_length=100)

    @field_validator("quotes")
    @classmethod
    def quotes_are_unique(
        cls, values: list[MarketQuoteRequest]
    ) -> list[MarketQuoteRequest]:
        if len({(value.asset_class, value.symbol) for value in values}) != len(values):
            raise ValueError("portfolio quotes must be unique")
        return values


class MarketAlertCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    condition: MarketAlertCondition
    threshold_minor: int = Field(strict=True, ge=1, le=10**15)


class MarketAlertEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: MarketQuoteRequest


class TradingJournalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160, pattern=_TEXT_PATTERN)
    note: str = Field(min_length=1, max_length=20_000, pattern=_TEXT_PATTERN)
    source_reference: str = Field(min_length=1, max_length=512, pattern=_TEXT_PATTERN)


class MarketWatchItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_class: MarketAssetClass
    symbol: str
    display_name: str
    created_at: datetime


class PaperPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_class: MarketAssetClass
    symbol: str
    quantity_micros: int
    cost_basis_minor: int
    updated_at: datetime


class PaperOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_class: MarketAssetClass
    symbol: str
    side: PaperOrderSide
    quantity_micros: int
    price_minor: int
    notional_minor: int
    source_reference: str
    observed_at: datetime
    status: PaperOrderStatus
    rejection_code: str | None
    cash_after_minor: int
    created_at: datetime
    execution_mode: Literal["paper"] = "paper"


class MarketAlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_class: MarketAssetClass
    symbol: str
    condition: MarketAlertCondition
    threshold_minor: int
    status: MarketAlertStatus
    last_price_minor: int | None
    last_source_reference: str | None
    last_observed_at: datetime | None
    created_at: datetime
    triggered_at: datetime | None


class FinanceArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: FinanceArtifactKind
    title: str
    source_reference: str
    input_sha256: str
    output: str
    output_sha256: str
    model_id: str
    duration_ms: int
    created_at: datetime


class FinanceWorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    base_currency: str
    initial_cash_minor: int
    cash_minor: int
    max_order_bps: int
    max_position_bps: int
    created_at: datetime
    updated_at: datetime
    watch_items: list[MarketWatchItemResponse]
    positions: list[PaperPositionResponse]
    orders: list[PaperOrderResponse]
    alerts: list[MarketAlertResponse]
    artifacts: list[FinanceArtifactResponse]
    execution_mode: Literal["paper"] = "paper"
    live_broker_status: Literal[
        "external_dependency", "configured_disabled", "live_enabled"
    ] = "external_dependency"


class FinanceWorkspacePageResponse(BaseModel):
    items: list[FinanceWorkspaceResponse] = Field(max_length=10)


class PortfolioAnalysisResponse(BaseModel):
    portfolio: FinanceArtifactResponse
    risk: FinanceArtifactResponse


class MarketAlertEvaluationResponse(BaseModel):
    items: list[MarketAlertResponse] = Field(max_length=100)


class MarketDataQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: UUID
    path: str = Field(min_length=1, max_length=512)
    max_age_seconds: int = Field(default=60, strict=True, ge=1, le=3_600)


class NormalizedMarketQuoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instrument_id: str
    asset_class: MarketAssetClass
    symbol: str
    exchange: str
    currency: str
    observed_at: datetime
    timezone: str
    last_price_minor: int
    bid_minor: int | None
    ask_minor: int | None
    open_minor: int | None
    high_minor: int | None
    low_minor: int | None
    close_minor: int | None
    volume: int | None
    provider: str
    source_reference: str
    freshness_seconds: int
    data_quality: Literal["fresh"]
    connector_execution_id: UUID


class TradingSafetyPolicyConfigureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broker_connector_id: UUID
    account_path: str = Field(min_length=1, max_length=512)
    order_path: str = Field(min_length=1, max_length=512)
    order_status_prefix: str = Field(min_length=1, max_length=512)
    max_order_value_minor: int = Field(strict=True, ge=1, le=10**15)
    max_position_value_minor: int = Field(strict=True, ge=1, le=10**15)
    daily_loss_limit_minor: int = Field(strict=True, ge=1, le=10**15)
    per_symbol_exposure_limit_minor: int = Field(strict=True, ge=1, le=10**15)
    total_exposure_limit_minor: int = Field(strict=True, ge=1, le=10**15)
    max_open_orders: int = Field(strict=True, ge=1, le=1_000)
    allowed_instruments: list[str] = Field(min_length=1, max_length=256)
    allowed_venues: list[str] = Field(min_length=1, max_length=64)
    owner_confirmation: Literal["AUTHORIZE BROKER CONFIGURATION"]

    @field_validator("allowed_instruments", "allowed_venues")
    @classmethod
    def policy_lists_are_unique(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("trading policy values must be unique")
        return values


class TradingSafetyToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)
    owner_confirmation: str = Field(min_length=1, max_length=64)


class TradingSafetyPolicyResponse(BaseModel):
    workspace_id: UUID
    execution_mode: TradingExecutionMode
    broker_connector_id: UUID | None
    broker_account_verified: bool
    live_trading_enabled: bool
    kill_switch_active: bool
    owner_authorized_at: datetime | None
    session_valid_until: datetime | None
    max_order_value_minor: int
    max_position_value_minor: int
    daily_loss_limit_minor: int
    per_symbol_exposure_limit_minor: int
    total_exposure_limit_minor: int
    max_open_orders: int
    allowed_instruments: list[str]
    allowed_venues: list[str]
    updated_at: datetime


class LiveBrokerOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_mode: Literal["live"]
    asset_class: MarketAssetClass
    symbol: str = Field(pattern=_SYMBOL_PATTERN)
    venue: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._:-]{0,31}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    side: PaperOrderSide
    quantity_micros: int = Field(strict=True, ge=1, le=10**15)
    limit_price_minor: int = Field(strict=True, ge=1, le=10**15)
    client_order_key: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$",
    )
    market_data_connector_id: UUID
    quote_path: str = Field(min_length=1, max_length=512)
    max_quote_age_seconds: int = Field(default=60, strict=True, ge=1, le=3_600)


class BrokerOrderRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    broker_connector_id: UUID
    asset_class: MarketAssetClass
    symbol: str
    venue: str
    currency: str
    side: PaperOrderSide
    quantity_micros: int
    limit_price_minor: int
    notional_minor: int
    filled_quantity_micros: int
    client_order_key_sha256: str
    request_sha256: str
    provider_order_sha256: str
    status: BrokerOrderStatus
    submit_execution_id: UUID
    status_execution_id: UUID
    created_at: datetime
    verified_at: datetime


class TradingSafetyEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    action: TradingSafetyAction
    policy_sha256: str
    connector_execution_id: UUID | None
    created_at: datetime


class TradingSafetyAuditResponse(BaseModel):
    events: list[TradingSafetyEventResponse] = Field(max_length=250)
    broker_orders: list[BrokerOrderRecordResponse] = Field(max_length=250)
