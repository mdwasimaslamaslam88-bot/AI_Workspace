from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.api.v1.finance as finance_module
from app.api.dependencies import get_current_user
from app.api.v1.finance import router
from app.db.dependencies import get_db_session
from app.finance.agent import MarketIntelligenceError
from app.models.user import User
from app.schemas.finance import (
    FinanceArtifactResponse,
    FinanceWorkspaceResponse,
    MarketAlertResponse,
    PaperOrderResponse,
)


def _artifact(kind="research") -> FinanceArtifactResponse:
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    return FinanceArtifactResponse(
        id=uuid4(),
        kind=kind,
        title="Verified artifact",
        source_reference="source.csv#row=2",
        input_sha256="a" * 64,
        output='{"grounded":true}',
        output_sha256="b" * 64,
        model_id="deterministic/test-agent-v1",
        duration_ms=1,
        created_at=now,
    )


def _workspace() -> FinanceWorkspaceResponse:
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    return FinanceWorkspaceResponse(
        id=uuid4(),
        name="Paper workspace",
        base_currency="USD",
        initial_cash_minor=100_000,
        cash_minor=100_000,
        max_order_bps=1_000,
        max_position_bps=2_500,
        created_at=now,
        updated_at=now,
        watch_items=[],
        positions=[],
        orders=[],
        alerts=[],
        artifacts=[],
    )


@pytest.fixture
def finance_api(monkeypatch):
    application = FastAPI()
    application.include_router(router, prefix="/api/v1")
    user = User(id=uuid4())
    session = AsyncMock(spec=AsyncSession)
    service = Mock()
    for method in (
        "list_workspaces",
        "create_workspace",
        "get_workspace",
        "add_watch_item",
        "remove_watch_item",
        "run_research",
        "run_backtest",
        "execute_paper_order",
        "analyze_portfolio",
        "create_alert",
        "evaluate_alerts",
        "add_journal_entry",
    ):
        setattr(service, method, AsyncMock())
    monkeypatch.setattr(finance_module, "FinanceService", Mock(return_value=service))
    monkeypatch.setattr(finance_module, "_market_agent", lambda _request: Mock())

    async def database_override():
        yield session

    async def user_override():
        return user

    application.dependency_overrides[get_db_session] = database_override
    application.dependency_overrides[get_current_user] = user_override
    with TestClient(application) as client:
        yield client, user, service


def test_finance_api_exposes_paper_only_grounded_lifecycle(finance_api):
    client, user, service = finance_api
    workspace = _workspace()
    artifact = _artifact()
    backtest = _artifact("backtest")
    portfolio = _artifact("portfolio")
    risk = _artifact("risk")
    now = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    order = PaperOrderResponse(
        id=uuid4(),
        asset_class="global_stock",
        symbol="ACME",
        side="buy",
        quantity_micros=1_000_000,
        price_minor=100,
        notional_minor=100,
        source_reference="exchange.csv#ACME",
        observed_at=now,
        status="executed",
        rejection_code=None,
        cash_after_minor=99_900,
        created_at=now,
    )
    alert = MarketAlertResponse(
        id=uuid4(),
        asset_class="global_stock",
        symbol="ACME",
        condition="at_or_above",
        threshold_minor=120,
        status="active",
        last_price_minor=None,
        last_source_reference=None,
        last_observed_at=None,
        created_at=now,
        triggered_at=None,
    )
    service.list_workspaces.return_value = (workspace,)
    service.create_workspace.return_value = workspace
    service.get_workspace.return_value = workspace
    service.add_watch_item.return_value = workspace
    service.remove_watch_item.return_value = workspace
    service.run_research.return_value = artifact
    service.run_backtest.return_value = backtest
    service.execute_paper_order.return_value = order
    service.analyze_portfolio.return_value = (portfolio, risk)
    service.create_alert.return_value = alert
    service.evaluate_alerts.return_value = (alert,)
    service.add_journal_entry.return_value = _artifact("journal")

    created = client.post(
        "/api/v1/finance/workspaces",
        json={
            "name": "Paper workspace",
            "base_currency": "USD",
            "initial_cash_minor": 100_000,
            "max_order_bps": 1_000,
            "max_position_bps": 2_500,
        },
    )
    assert created.status_code == 201
    assert created.json()["execution_mode"] == "paper"
    assert created.json()["live_broker_status"] == "external_dependency"
    workspace_id = workspace.id
    assert client.get("/api/v1/finance/workspaces").status_code == 200
    assert client.get(f"/api/v1/finance/workspaces/{workspace_id}").status_code == 200
    assert client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/watchlist",
        json={"asset_class": "global_stock", "symbol": "ACME", "display_name": "ACME"},
    ).status_code == 200
    assert client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/research",
        json={
            "kind": "research",
            "asset_class": "global_stock",
            "subject": "ACME filing",
            "source_reference": "filing.pdf#p2",
            "source_facts": [{"source_reference": "filing.pdf#p2", "fact": "Revenue was reported."}],
        },
    ).status_code == 200
    bars = [
        {"observed_at": f"2026-01-0{day}T00:00:00Z", "close_minor": value}
        for day, value in enumerate((100, 90, 80), start=1)
    ]
    assert client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/backtests",
        json={
            "asset_class": "global_stock",
            "symbol": "ACME",
            "source_reference": "history.csv",
            "bars": bars,
            "fast_window": 2,
            "slow_window": 3,
            "initial_cash_minor": 100_000,
            "fee_bps": 0,
        },
    ).status_code == 200
    paper = client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/paper-orders",
        json={
            "execution_mode": "paper",
            "asset_class": "global_stock",
            "symbol": "ACME",
            "side": "buy",
            "quantity_micros": 1_000_000,
            "price_minor": 100,
            "observed_at": "2026-09-03T12:00:00Z",
            "source_reference": "exchange.csv#ACME",
            "owner_confirmed": True,
        },
    )
    assert paper.status_code == 200
    assert paper.json()["execution_mode"] == "paper"
    assert service.execute_paper_order.await_args.args[0] == user.id


def test_finance_api_rejects_live_orders_and_hides_owner_existence(finance_api):
    client, _user, service = finance_api
    workspace_id = uuid4()
    live = client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/paper-orders",
        json={
            "execution_mode": "live",
            "asset_class": "crypto",
            "symbol": "BTC/USD",
            "side": "buy",
            "quantity_micros": 1,
            "price_minor": 1,
            "observed_at": "2026-09-03T12:00:00Z",
            "source_reference": "owner-source",
            "owner_confirmed": True,
        },
    )
    assert live.status_code == 422
    service.get_workspace.return_value = None
    missing = client.get(f"/api/v1/finance/workspaces/{workspace_id}")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Finance resource not found"}


def test_market_agent_failure_is_safe_and_fixed(finance_api):
    client, _user, service = finance_api
    service.run_research.side_effect = MarketIntelligenceError("PRIVATE_MODEL_SENTINEL")
    response = client.post(
        f"/api/v1/finance/workspaces/{uuid4()}/research",
        json={
            "kind": "research",
            "asset_class": "fx",
            "subject": "Supplied FX observation",
            "source_reference": "source.csv",
            "source_facts": [{"source_reference": "source.csv", "fact": "A fact."}],
        },
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "Verified local market research failed"}
    assert "PRIVATE_MODEL_SENTINEL" not in response.text


def test_external_market_and_broker_routes_fail_closed_without_runtime(finance_api):
    client, _user, _service = finance_api
    workspace_id = uuid4()
    connector_id = uuid4()

    quote = client.post(
        "/api/v1/finance/market-data/quotes/resolve",
        json={"connector_id": str(connector_id), "path": "/quotes/ACME"},
    )
    assert quote.status_code == 409
    assert quote.json() == {"detail": "Finance provider runtime is not configured"}

    configuration = client.put(
        f"/api/v1/finance/workspaces/{workspace_id}/trading-safety",
        json={
            "broker_connector_id": str(connector_id),
            "account_path": "/broker/account",
            "order_path": "/broker/orders",
            "order_status_prefix": "/broker/orders/status/",
            "max_order_value_minor": 10_000,
            "max_position_value_minor": 20_000,
            "daily_loss_limit_minor": 5_000,
            "per_symbol_exposure_limit_minor": 20_000,
            "total_exposure_limit_minor": 50_000,
            "max_open_orders": 3,
            "allowed_instruments": ["ACME"],
            "allowed_venues": ["NASDAQ"],
            "owner_confirmation": "AUTHORIZE BROKER CONFIGURATION",
        },
    )
    assert configuration.status_code == 409
    assert configuration.json() == {"detail": "Broker provider runtime is not configured"}

    live_order = client.post(
        f"/api/v1/finance/workspaces/{workspace_id}/broker-orders",
        json={
            "execution_mode": "live",
            "asset_class": "global_stock",
            "symbol": "ACME",
            "venue": "NASDAQ",
            "currency": "USD",
            "side": "buy",
            "quantity_micros": 1_000_000,
            "limit_price_minor": 10_000,
            "client_order_key": "owner-order-00000001",
            "market_data_connector_id": str(connector_id),
            "quote_path": "/quotes/ACME",
        },
    )
    assert live_order.status_code == 409
    assert live_order.json() == {"detail": "Broker provider runtime is not configured"}
