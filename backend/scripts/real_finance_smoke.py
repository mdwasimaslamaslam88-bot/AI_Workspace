from __future__ import annotations

import asyncio
import hashlib
import io
import logging

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clients.postgres import create_postgres_engine, dispose_postgres
from app.core.config import settings
from app.main import app
from scripts.runtime_smoke_safety import select_disposable_runtime_database


_PRIVATE_SOURCE_FACT = "The supplied filing excerpt reports revenue of 125000 minor units."
_OBSERVED_AT = "2026-09-03T12:00:00Z"


async def _clean_disposable_database() -> None:
    engine = create_postgres_engine(settings)
    if engine is None:
        raise RuntimeError("disposable database engine is unavailable")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    finally:
        await dispose_postgres(engine)


def _provision(client: TestClient, token: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users",
        headers={"X-User-Provisioning-Token": token},
        json={},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _post(client: TestClient, path: str, headers: dict[str, str], payload: dict) -> dict:
    response = client.post(path, headers=headers, json=payload)
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"finance runtime request failed safely: {path} ({response.status_code})")
    return response.json()


def main() -> None:
    select_disposable_runtime_database(settings)
    asyncio.run(_clean_disposable_database())
    provisioning_token = "f" * 43
    settings.USER_PROVISIONING_TOKEN_DIGEST = hashlib.sha256(
        provisioning_token.encode("utf-8")
    ).hexdigest()
    captured_logs = io.StringIO()
    handler = logging.StreamHandler(captured_logs)
    logging.getLogger().addHandler(handler)
    try:
        with TestClient(app) as client:
            owner = _provision(client, provisioning_token)
            foreign = _provision(client, provisioning_token)
            workspace = _post(
                client,
                "/api/v1/finance/workspaces",
                owner,
                {
                    "name": "Verified paper market lab",
                    "base_currency": "USD",
                    "initial_cash_minor": 1_000_000,
                    "max_order_bps": 1_000,
                    "max_position_bps": 2_500,
                },
            )
            if workspace["execution_mode"] != "paper" or workspace["live_broker_status"] != "external_dependency":
                raise RuntimeError("finance workspace did not preserve the paper-only boundary")
            workspace_id = workspace["id"]
            if client.get(f"/api/v1/finance/workspaces/{workspace_id}", headers=foreign).status_code != 404:
                raise RuntimeError("foreign owner could inspect a finance workspace")

            _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/watchlist",
                owner,
                {"asset_class": "global_stock", "symbol": "ACME", "display_name": "ACME Paper"},
            )
            research = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/research",
                owner,
                {
                    "kind": "research",
                    "asset_class": "global_stock",
                    "subject": "ACME supplied filing excerpt",
                    "source_reference": "owner-filing.txt#excerpt",
                    "source_facts": [{
                        "source_reference": "owner-filing.txt#excerpt",
                        "fact": _PRIVATE_SOURCE_FACT,
                    }],
                },
            )
            if (
                not research["model_id"]
                or research["output_sha256"] != hashlib.sha256(research["output"].encode("utf-8")).hexdigest()
                or research["source_reference"] != "owner-filing.txt#excerpt"
            ):
                raise RuntimeError("real market research verifier evidence is incomplete")
            strategy = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/research",
                owner,
                {
                    "kind": "strategy",
                    "asset_class": "global_stock",
                    "subject": "ACME paper-only hypothesis",
                    "source_reference": "owner-filing.txt#excerpt",
                    "source_facts": [{
                        "source_reference": "owner-filing.txt#excerpt",
                        "fact": _PRIVATE_SOURCE_FACT,
                    }],
                },
            )
            if (
                not strategy["model_id"]
                or strategy["output_sha256"]
                != hashlib.sha256(strategy["output"].encode("utf-8")).hexdigest()
            ):
                raise RuntimeError("real paper strategy verifier evidence is incomplete")

            bars = [
                {"observed_at": f"2026-08-{day:02d}T00:00:00Z", "close_minor": price}
                for day, price in enumerate((100, 90, 95, 110, 105), start=1)
            ]
            backtest = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/backtests",
                owner,
                {
                    "asset_class": "global_stock", "symbol": "ACME",
                    "source_reference": "owner-history.csv#ACME", "bars": bars,
                    "fast_window": 2, "slow_window": 3,
                    "initial_cash_minor": 100_000, "fee_bps": 5,
                },
            )
            if '"profit_guarantee":false' not in backtest["output"]:
                raise RuntimeError("backtest omitted its no-profit-guarantee evidence")

            order_base = {
                "execution_mode": "paper", "asset_class": "global_stock",
                "symbol": "ACME", "side": "buy", "quantity_micros": 1_000_000,
                "price_minor": 10_000, "observed_at": _OBSERVED_AT,
                "source_reference": "owner-quote.csv#ACME",
            }
            rejected = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/paper-orders",
                owner,
                {**order_base, "owner_confirmed": False},
            )
            if rejected["status"] != "rejected" or rejected["rejection_code"] != "owner_confirmation_required":
                raise RuntimeError("unconfirmed paper order was not rejected")
            executed = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/paper-orders",
                owner,
                {**order_base, "owner_confirmed": True},
            )
            if executed["status"] != "executed" or executed["execution_mode"] != "paper":
                raise RuntimeError("confirmed paper simulation did not execute")

            analysis = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/portfolio-analysis",
                owner,
                {
                    "source_reference": "owner-quote.csv#ACME",
                    "quotes": [{
                        "asset_class": "global_stock", "symbol": "ACME",
                        "price_minor": 11_000, "observed_at": _OBSERVED_AT,
                        "source_reference": "owner-quote.csv#ACME",
                    }],
                },
            )
            if analysis["portfolio"]["model_id"] != "deterministic/portfolio-agent-v1" or analysis["risk"]["model_id"] != "deterministic/risk-agent-v1":
                raise RuntimeError("portfolio or risk agent evidence is incomplete")

            alert = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/alerts",
                owner,
                {"asset_class": "global_stock", "symbol": "ACME", "condition": "at_or_above", "threshold_minor": 10_500},
            )
            evaluated = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/alerts/evaluate",
                owner,
                {"quote": {"asset_class": "global_stock", "symbol": "ACME", "price_minor": 11_000, "observed_at": _OBSERVED_AT, "source_reference": "owner-quote.csv#ACME"}},
            )
            if alert["status"] != "active" or evaluated["items"][0]["status"] != "triggered":
                raise RuntimeError("sourced market alert did not transition truthfully")

            journal = _post(
                client,
                f"/api/v1/finance/workspaces/{workspace_id}/journal",
                owner,
                {"title": "Paper decision", "note": "Observed and simulated; no live order.", "source_reference": "owner-quote.csv#ACME"},
            )
            if journal["model_id"] != "deterministic/trading-journal-agent-v1":
                raise RuntimeError("trading journal did not persist deterministically")

            final = client.get(f"/api/v1/finance/workspaces/{workspace_id}", headers=owner)
            final.raise_for_status()
            record = final.json()
            if len(record["orders"]) != 2 or len(record["positions"]) != 1 or len(record["artifacts"]) < 6:
                raise RuntimeError("finance workspace did not persist its verified history")
    finally:
        logging.getLogger().removeHandler(handler)

    if _PRIVATE_SOURCE_FACT in captured_logs.getvalue():
        raise RuntimeError("private market source leaked into logs")
    print("REAL_FINANCE_MARKET_RESEARCH=passed")
    print("REAL_FINANCE_PAPER_STRATEGY=passed")
    print("FINANCE_BACKTEST_AND_PAPER_TRADING=passed")
    print("FINANCE_PORTFOLIO_RISK_ALERTS_JOURNAL=passed")
    print("LIVE_BROKER_BOUNDARY=external_dependency")


if __name__ == "__main__":
    main()
