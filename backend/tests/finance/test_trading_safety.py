from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.connectors.service import ConnectorPermissionError, ConnectorService
from app.finance.market_data import MarketDataQuality, NormalizedMarketQuote
from app.finance.service import FinanceConflictError
from app.finance.trading import (
    BrokerAccountSnapshot,
    BrokerTradingService,
    LiveOrderCommand,
    LiveTradingSafetyWall,
)
from app.models.connector import (
    Connector,
    ConnectorAction,
    ConnectorAuthKind,
    ConnectorHealthStatus,
    ConnectorKind,
)
from app.models.finance import (
    BrokerOrderStatus,
    MarketAssetClass,
    PaperOrderSide,
    TradingExecutionMode,
    TradingSafetyPolicy,
)


NOW = datetime(2026, 9, 4, 10, tzinfo=timezone.utc)


def _policy() -> TradingSafetyPolicy:
    return TradingSafetyPolicy(
        workspace_id=uuid4(),
        owner_id=uuid4(),
        execution_mode=TradingExecutionMode.LIVE,
        broker_connector_id=uuid4(),
        broker_account_sha256="2bd806c97f0e00af1a1fc3328fa763a9269723c8db8fac4f93af71db186d6e90",
        account_path="/broker/account",
        order_path="/broker/orders",
        order_status_prefix="/broker/orders/status/",
        live_trading_enabled=True,
        kill_switch_active=False,
        owner_authorized_at=NOW,
        session_valid_until=NOW + timedelta(minutes=5),
        max_order_value_minor=20_000,
        max_position_value_minor=40_000,
        daily_loss_limit_minor=10_000,
        per_symbol_exposure_limit_minor=40_000,
        total_exposure_limit_minor=100_000,
        max_open_orders=3,
        allowed_instruments_json='["ACME"]',
        allowed_venues_json='["NASDAQ"]',
    )


def _account() -> BrokerAccountSnapshot:
    return BrokerAccountSnapshot(
        account_id="alice",
        currency="USD",
        session_valid_until=NOW + timedelta(minutes=5),
        daily_pnl_minor=0,
        total_exposure_minor=5_000,
        open_orders=0,
        position_values_minor={},
        market_session_open=True,
    )


def _quote() -> NormalizedMarketQuote:
    return NormalizedMarketQuote(
        instrument_id="global_stock:NASDAQ:ACME",
        asset_class=MarketAssetClass.GLOBAL_STOCK,
        symbol="ACME",
        exchange="NASDAQ",
        currency="USD",
        observed_at=NOW,
        timezone="UTC",
        last_price_minor=10_000,
        bid_minor=9_999,
        ask_minor=10_001,
        open_minor=9_900,
        high_minor=10_100,
        low_minor=9_800,
        close_minor=10_000,
        volume=123,
        provider="owner-feed",
        source_reference=f"connector-execution:{uuid4()}",
        freshness_seconds=0,
        data_quality=MarketDataQuality.FRESH,
        connector_execution_id=uuid4(),
    )


def _command() -> LiveOrderCommand:
    return LiveOrderCommand(
        asset_class=MarketAssetClass.GLOBAL_STOCK,
        symbol="ACME",
        venue="NASDAQ",
        currency="USD",
        side=PaperOrderSide.BUY,
        quantity_micros=1_000_000,
        limit_price_minor=10_000,
        client_order_key="owner-order-00000001",
        market_data_connector_id=uuid4(),
        quote_path="/quotes/ACME",
    )


def test_live_safety_wall_accepts_only_fully_bounded_order():
    assert LiveTradingSafetyWall.require(
        _policy(), _command(), _account(), _quote(), now=NOW
    ) == 10_000


def test_live_safety_wall_rejects_sell_beyond_verified_position():
    command = _command()
    object.__setattr__(command, "side", PaperOrderSide.SELL)
    with pytest.raises(FinanceConflictError, match="verified position"):
        LiveTradingSafetyWall.require(
            _policy(), command, _account(), _quote(), now=NOW
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p, _a, _c, _q: setattr(p, "execution_mode", TradingExecutionMode.PAPER), "mode"),
        (lambda p, _a, _c, _q: setattr(p, "live_trading_enabled", False), "authorization"),
        (lambda p, _a, _c, _q: setattr(p, "kill_switch_active", True), "kill switch"),
        (lambda p, _a, _c, _q: setattr(p, "max_order_value_minor", 1), "order value"),
        (lambda p, a, _c, _q: object.__setattr__(a, "daily_pnl_minor", -20_000), "daily loss"),
        (lambda p, a, _c, _q: object.__setattr__(a, "open_orders", p.max_open_orders), "open orders"),
        (lambda _p, a, _c, _q: object.__setattr__(a, "market_session_open", False), "session is closed"),
        (lambda _p, _a, c, _q: object.__setattr__(c, "symbol", "OTHER"), "not owner-authorized"),
        (lambda _p, _a, _c, q: object.__setattr__(q, "exchange", "NYSE"), "venue"),
    ],
)
def test_live_safety_wall_fails_closed_for_each_policy_boundary(change, message):
    policy, account, command, quote = _policy(), _account(), _command(), _quote()
    change(policy, account, command, quote)
    with pytest.raises(FinanceConflictError, match=message):
        LiveTradingSafetyWall.require(policy, command, account, quote, now=NOW)


def test_generic_connector_execution_cannot_bypass_broker_gateway():
    connector = Connector(
        id=uuid4(),
        owner_id=uuid4(),
        name="Broker",
        provider="owner-broker",
        service="broker",
        kind=ConnectorKind.REST,
        base_url="https://broker.example",
        auth_kind=ConnectorAuthKind.BEARER,
        credential_ciphertext="encrypted",
        scopes_json='["read","write"]',
        path_prefixes_json='["/broker/"]',
        health_path="/broker/health",
        enabled=True,
        health_status=ConnectorHealthStatus.HEALTHY,
        capabilities_json='["broker.account.read","broker.order.status","broker.order.submit"]',
        timeout_seconds=5,
        max_retries=1,
        rate_limit_requests_per_minute=10,
    )
    with pytest.raises(ConnectorPermissionError):
        ConnectorService._authorize(
            connector,
            "POST",
            "/broker/orders",
            action=ConnectorAction.EXECUTE,
        )
    ConnectorService._authorize(
        connector,
        "POST",
        "/broker/orders",
        action=ConnectorAction.EXECUTE,
        required_capability="broker.order.submit",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_status", "filled_quantity", "expected_status"),
    [
        ("open", 500_000, BrokerOrderStatus.VERIFIED_OPEN),
        ("filled", 1_000_000, BrokerOrderStatus.VERIFIED_FILLED),
    ],
)
async def test_verified_broker_order_requires_ack_status_and_is_idempotent(
    provider_status, filled_quantity, expected_status
):
    owner_id = uuid4()
    policy = _policy()
    policy.owner_id = owner_id
    command = _command()
    account = _account()
    quote = _quote()
    connector_service = AsyncMock()
    submit_id, status_id = uuid4(), uuid4()
    connector_service.execute_for_owner.side_effect = [
        SimpleNamespace(
            execution=SimpleNamespace(id=submit_id),
            payload={
                "provider": "owner-broker",
                "account_id": "alice",
                "client_order_id": command.client_order_key,
                "order_id": "provider-order-1",
                "status": "accepted",
                "status_path": "/broker/orders/status/1",
            },
        ),
        SimpleNamespace(
            execution=SimpleNamespace(id=status_id),
            payload={
                "provider": "owner-broker",
                "account_id": "alice",
                "client_order_id": command.client_order_key,
                "order_id": "provider-order-1",
                "status": provider_status,
                "filled_quantity_micros": filled_quantity,
            },
        ),
    ]
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, connector_service)
    service.repository = SimpleNamespace(
        get_broker_order_by_client_hash=AsyncMock(return_value=None),
        get_trading_policy=AsyncMock(return_value=policy),
    )
    service._account = AsyncMock(return_value=(account, uuid4(), "owner-broker"))
    service.market_data = SimpleNamespace(quote=AsyncMock(return_value=quote))

    record = await service.place_live_order(owner_id, policy.workspace_id, command, now=NOW)

    assert record.status is expected_status
    assert record.filled_quantity_micros == filled_quantity
    assert record.submit_execution_id == submit_id
    assert record.status_execution_id == status_id
    assert record.client_order_key_sha256 != command.client_order_key
    assert session.add.call_count == 1
    assert session.commit.await_count == 1
    calls = connector_service.execute_for_owner.await_args_list
    assert calls[0].kwargs["required_capability"] == "broker.order.submit"
    assert calls[0].kwargs["idempotency_key"] == command.client_order_key
    assert calls[1].kwargs["required_capability"] == "broker.order.status"

    service.repository.get_broker_order_by_client_hash.return_value = record
    repeated = await service.place_live_order(owner_id, policy.workspace_id, command, now=NOW)
    assert repeated is record
    assert connector_service.execute_for_owner.await_count == 2
    assert session.commit.await_count == 2


@pytest.mark.asyncio
async def test_kill_switch_disables_live_execution_without_network_access():
    policy = _policy()
    repository = SimpleNamespace(get_trading_policy=AsyncMock(return_value=policy))
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, AsyncMock())
    service.repository = repository

    result = await service.activate_kill_switch(policy.owner_id, policy.workspace_id, now=NOW)

    assert result.kill_switch_active is True
    assert result.live_trading_enabled is False
    assert session.add.call_count == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_configuration_verifies_account_then_defaults_to_disabled():
    owner_id, workspace_id, connector_id = uuid4(), uuid4(), uuid4()
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, AsyncMock())
    service.repository = SimpleNamespace(
        get_workspace_for_owner=AsyncMock(return_value=SimpleNamespace(id=workspace_id)),
        get_trading_policy=AsyncMock(return_value=None),
    )
    account_execution_id = uuid4()
    service._account = AsyncMock(
        return_value=(_account(), account_execution_id, "owner-broker")
    )

    policy = await service.configure_policy(
        owner_id,
        workspace_id,
        broker_connector_id=connector_id,
        account_path="/broker/account",
        order_path="/broker/orders",
        order_status_prefix="/broker/orders/status/",
        max_order_value_minor=10_000,
        max_position_value_minor=20_000,
        daily_loss_limit_minor=5_000,
        per_symbol_exposure_limit_minor=20_000,
        total_exposure_limit_minor=50_000,
        max_open_orders=3,
        allowed_instruments=("ACME",),
        allowed_venues=("NASDAQ",),
        owner_confirmation="AUTHORIZE BROKER CONFIGURATION",
        now=NOW,
    )

    assert policy.execution_mode is TradingExecutionMode.LIVE
    assert policy.live_trading_enabled is False
    assert policy.kill_switch_active is True
    assert policy.broker_account_sha256 == _policy().broker_account_sha256
    assert session.add.call_count == 2  # policy plus immutable safety event
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_enable_requires_exact_confirmation_and_rechecks_account():
    policy = _policy()
    policy.live_trading_enabled = False
    policy.kill_switch_active = True
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, AsyncMock())
    service.repository = SimpleNamespace(get_trading_policy=AsyncMock(return_value=policy))
    service._account = AsyncMock(return_value=(_account(), uuid4(), "owner-broker"))

    with pytest.raises(FinanceConflictError, match="exact live-trading"):
        await service.set_live_enabled(
            policy.owner_id,
            policy.workspace_id,
            enabled=True,
            owner_confirmation="yes",
            now=NOW,
        )
    service._account.assert_not_awaited()

    result = await service.set_live_enabled(
        policy.owner_id,
        policy.workspace_id,
        enabled=True,
        owner_confirmation="ENABLE LIVE TRADING",
        now=NOW,
    )
    assert result.live_trading_enabled is True
    assert result.kill_switch_active is False
    service._account.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_blocks_submit_even_when_read_preflights_succeed():
    policy = _policy()
    policy.kill_switch_active = True
    owner_id = policy.owner_id
    connector_service = AsyncMock()
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, connector_service)
    service.repository = SimpleNamespace(
        get_broker_order_by_client_hash=AsyncMock(return_value=None),
        get_trading_policy=AsyncMock(return_value=policy),
    )
    service._account = AsyncMock(return_value=(_account(), uuid4(), "owner-broker"))
    service.market_data = SimpleNamespace(quote=AsyncMock(return_value=_quote()))

    with pytest.raises(FinanceConflictError, match="kill switch"):
        await service.place_live_order(owner_id, policy.workspace_id, _command(), now=NOW)
    service._account.assert_not_awaited()
    service.market_data.quote.assert_not_awaited()
    connector_service.execute_for_owner.assert_not_awaited()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_http_success_without_matching_receipt_is_not_an_order():
    policy = _policy()
    command = _command()
    connector_service = AsyncMock()
    connector_service.execute_for_owner.return_value = SimpleNamespace(
        execution=SimpleNamespace(id=uuid4()), payload={"ok": True}
    )
    session = SimpleNamespace(add=Mock(), commit=AsyncMock())
    service = BrokerTradingService(session, connector_service)
    service.repository = SimpleNamespace(
        get_broker_order_by_client_hash=AsyncMock(return_value=None),
        get_trading_policy=AsyncMock(return_value=policy),
    )
    service._account = AsyncMock(return_value=(_account(), uuid4(), "owner-broker"))
    service.market_data = SimpleNamespace(quote=AsyncMock(return_value=_quote()))

    with pytest.raises(FinanceConflictError, match="acknowledgement"):
        await service.place_live_order(
            policy.owner_id, policy.workspace_id, command, now=NOW
        )
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
