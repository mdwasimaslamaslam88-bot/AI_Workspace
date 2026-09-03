from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.finance.market_data import MarketDataGateway, MarketDataQuality, normalize_market_quote
from app.finance.service import FinanceInputError


NOW = datetime(2026, 9, 4, 10, tzinfo=timezone.utc)


def _payload(asset_class: str = "global_stock"):
    return {
        "provider": "owner-feed",
        "instrument": {
            "asset_class": asset_class,
            "symbol": "ACME" if asset_class != "crypto" else "BTC/USD",
            "exchange": "NASDAQ" if asset_class != "crypto" else "OWNERX",
            "currency": "USD",
        },
        "quote": {
            "timestamp": (NOW - timedelta(seconds=4)).isoformat(),
            "timezone": "UTC",
            "last_minor": 10_000,
            "bid_minor": 9_999,
            "ask_minor": 10_001,
            "open_minor": 9_900,
            "high_minor": 10_100,
            "low_minor": 9_800,
            "close_minor": 10_000,
            "volume": 123,
        },
    }


@pytest.mark.parametrize("asset_class", ["indian_stock", "global_stock", "crypto", "fx"])
def test_normalized_quote_covers_supported_assets_with_attribution(asset_class):
    execution_id = uuid4()
    result = normalize_market_quote(
        _payload(asset_class),
        expected_provider="owner-feed",
        execution_id=execution_id,
        now=NOW,
        max_age_seconds=10,
    )

    assert result.asset_class.value == asset_class
    assert result.data_quality is MarketDataQuality.FRESH
    assert result.freshness_seconds == 4
    assert result.connector_execution_id == execution_id
    assert result.source_reference == f"connector-execution:{execution_id}"


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda value: value.update(provider="another-feed"), "identity mismatch"),
        (
            lambda value: value["quote"].update(
                timestamp=(NOW - timedelta(minutes=2)).isoformat()
            ),
            "stale",
        ),
        (lambda value: value["quote"].update(bid_minor=10_002), "spread"),
        (lambda value: value["quote"].update(untrusted=1), "contract"),
    ],
)
def test_normalized_quote_fails_closed_for_bad_provider_data(mutation, error):
    payload = _payload()
    mutation(payload)
    with pytest.raises(FinanceInputError, match=error):
        normalize_market_quote(
            payload,
            expected_provider="owner-feed",
            execution_id=uuid4(),
            now=NOW,
            max_age_seconds=30,
        )


@pytest.mark.asyncio
async def test_market_gateway_requires_owner_connector_capability_and_audit():
    owner_id = uuid4()
    connector_id = uuid4()
    execution_id = uuid4()
    service = AsyncMock()
    service.get_for_owner.return_value = SimpleNamespace(provider="owner-feed")
    service.execute_for_owner.return_value = SimpleNamespace(
        payload=_payload(), execution=SimpleNamespace(id=execution_id)
    )

    quote = await MarketDataGateway(service).quote(
        owner_id,
        connector_id,
        path="/v1/quotes/ACME",
        now=NOW,
    )

    assert quote.connector_execution_id == execution_id
    service.execute_for_owner.assert_awaited_once_with(
        owner_id,
        connector_id,
        method="GET",
        path="/v1/quotes/ACME",
        json_body=None,
        idempotency_key=None,
        required_capability="market.quote.read",
    )
