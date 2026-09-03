from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any
from uuid import UUID

from app.connectors.service import ConnectorExecutionResult, ConnectorService
from app.finance.service import FinanceInputError
from app.models.finance import MarketAssetClass


_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._:/-]{0,23}$")
_VENUE = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,31}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_EXPECTED_TOP_LEVEL = frozenset({"provider", "instrument", "quote"})
_EXPECTED_INSTRUMENT = frozenset({"asset_class", "symbol", "exchange", "currency"})
_REQUIRED_QUOTE = frozenset({"timestamp", "timezone", "last_minor"})
_OPTIONAL_QUOTE = frozenset(
    {"bid_minor", "ask_minor", "open_minor", "high_minor", "low_minor", "close_minor", "volume"}
)


class MarketDataQuality(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class NormalizedMarketQuote:
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
    data_quality: MarketDataQuality
    connector_execution_id: UUID


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise FinanceInputError(f"market data {label} is invalid")
    return value


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not 1 <= len(value) <= maximum
        or any(ord(character) < 0x20 for character in value)
    ):
        raise FinanceInputError(f"market data {label} is invalid")
    return value


def _optional_integer(value: Any, label: str, *, positive: bool = True) -> int | None:
    if value is None:
        return None
    lower = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= 10**15:
        raise FinanceInputError(f"market data {label} is invalid")
    return value


def normalize_market_quote(
    payload: Any,
    *,
    expected_provider: str,
    execution_id: UUID,
    now: datetime | None = None,
    max_age_seconds: int = 60,
) -> NormalizedMarketQuote:
    """Validate an attributed provider quote before it reaches finance logic."""

    if isinstance(max_age_seconds, bool) or not 1 <= max_age_seconds <= 3_600:
        raise FinanceInputError("market data freshness bound is invalid")
    document = _mapping(payload, "response")
    if frozenset(document) != _EXPECTED_TOP_LEVEL:
        raise FinanceInputError("market data response contract is invalid")
    provider = _bounded_text(document["provider"], "provider", 120)
    if provider != expected_provider:
        raise FinanceInputError("market data provider identity mismatch")
    instrument = _mapping(document["instrument"], "instrument")
    if frozenset(instrument) != _EXPECTED_INSTRUMENT:
        raise FinanceInputError("market data instrument contract is invalid")
    try:
        asset_class = MarketAssetClass(instrument["asset_class"])
    except (TypeError, ValueError) as exc:
        raise FinanceInputError("market data asset class is invalid") from exc
    symbol = _bounded_text(instrument["symbol"], "symbol", 24)
    venue = _bounded_text(instrument["exchange"], "exchange", 32)
    currency = _bounded_text(instrument["currency"], "currency", 3)
    if not _SYMBOL.fullmatch(symbol) or not _VENUE.fullmatch(venue) or not _CURRENCY.fullmatch(currency):
        raise FinanceInputError("market data instrument identity is invalid")

    quote = _mapping(document["quote"], "quote")
    quote_keys = frozenset(quote)
    if not _REQUIRED_QUOTE <= quote_keys or quote_keys - (_REQUIRED_QUOTE | _OPTIONAL_QUOTE):
        raise FinanceInputError("market data quote contract is invalid")
    observed_raw = _bounded_text(quote["timestamp"], "timestamp", 64)
    try:
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FinanceInputError("market data timestamp is invalid") from exc
    if observed_at.tzinfo is None:
        raise FinanceInputError("market data timestamp requires a timezone")
    timezone_name = _bounded_text(quote["timezone"], "timezone", 64)
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        raise FinanceInputError("market data validation clock requires a timezone")
    age = int((reference_now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds())
    if age < -5:
        raise FinanceInputError("market data timestamp is in the future")
    if age > max_age_seconds:
        raise FinanceInputError("market data is stale")

    last = _optional_integer(quote["last_minor"], "last price")
    assert last is not None
    bid = _optional_integer(quote.get("bid_minor"), "bid")
    ask = _optional_integer(quote.get("ask_minor"), "ask")
    opening = _optional_integer(quote.get("open_minor"), "open")
    high = _optional_integer(quote.get("high_minor"), "high")
    low = _optional_integer(quote.get("low_minor"), "low")
    close = _optional_integer(quote.get("close_minor"), "close")
    volume = _optional_integer(quote.get("volume"), "volume", positive=False)
    if bid is not None and ask is not None and bid > ask:
        raise FinanceInputError("market data spread is invalid")
    bounded_prices = [value for value in (opening, close, last, bid, ask) if value is not None]
    if low is not None and any(value < low for value in bounded_prices):
        raise FinanceInputError("market data low is invalid")
    if high is not None and any(value > high for value in bounded_prices):
        raise FinanceInputError("market data high is invalid")
    if high is not None and low is not None and high < low:
        raise FinanceInputError("market data range is invalid")

    return NormalizedMarketQuote(
        instrument_id=f"{asset_class.value}:{venue}:{symbol}",
        asset_class=asset_class,
        symbol=symbol,
        exchange=venue,
        currency=currency,
        observed_at=observed_at,
        timezone=timezone_name,
        last_price_minor=last,
        bid_minor=bid,
        ask_minor=ask,
        open_minor=opening,
        high_minor=high,
        low_minor=low,
        close_minor=close,
        volume=volume,
        provider=provider,
        source_reference=f"connector-execution:{execution_id}",
        freshness_seconds=max(0, age),
        data_quality=MarketDataQuality.FRESH,
        connector_execution_id=execution_id,
    )


class MarketDataGateway:
    def __init__(self, connector_service: ConnectorService) -> None:
        self.connector_service = connector_service

    async def quote(
        self,
        owner_id: UUID,
        connector_id: UUID,
        *,
        path: str,
        max_age_seconds: int = 60,
        now: datetime | None = None,
    ) -> NormalizedMarketQuote:
        connector = await self.connector_service.get_for_owner(owner_id, connector_id)
        result: ConnectorExecutionResult = await self.connector_service.execute_for_owner(
            owner_id,
            connector_id,
            method="GET",
            path=path,
            json_body=None,
            idempotency_key=None,
            required_capability="market.quote.read",
        )
        return normalize_market_quote(
            result.payload,
            expected_provider=connector.provider,
            execution_id=result.execution.id,
            now=now,
            max_age_seconds=max_age_seconds,
        )
