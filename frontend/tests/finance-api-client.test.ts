import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import type { PaperOrderRequest } from "../src/api/contracts";
import { jsonResponse, token } from "./fixtures";

const workspaceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const itemId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const timestamp = "2026-09-03T00:00:00Z";

function artifact(kind = "research") {
  return {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    kind,
    title: "Verified finance result",
    source_reference: "owner.csv#row=2",
    input_sha256: "a".repeat(64),
    output: "Grounded output.",
    output_sha256: "b".repeat(64),
    model_id: kind === "backtest" ? "deterministic/backtesting-agent" : "local/qwen3",
    duration_ms: 12,
    created_at: timestamp,
  };
}

function workspace() {
  return {
    id: workspaceId,
    name: "Paper portfolio",
    base_currency: "USD",
    initial_cash_minor: 1_000_000,
    cash_minor: 900_000,
    max_order_bps: 1_000,
    max_position_bps: 2_500,
    created_at: timestamp,
    updated_at: timestamp,
    watch_items: [],
    positions: [],
    orders: [],
    alerts: [],
    artifacts: [],
    execution_mode: "paper",
    live_broker_status: "external_dependency",
  };
}

function order() {
  return {
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    asset_class: "global_stock",
    symbol: "AAPL",
    side: "buy",
    quantity_micros: 1_000_000,
    price_minor: 10_000,
    notional_minor: 10_000,
    source_reference: "owner.csv#row=2",
    observed_at: timestamp,
    status: "executed",
    rejection_code: null,
    cash_after_minor: 990_000,
    created_at: timestamp,
    execution_mode: "paper",
  };
}

function alert() {
  return {
    id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    asset_class: "global_stock",
    symbol: "AAPL",
    condition: "at_or_above",
    threshold_minor: 10_000,
    status: "active",
    last_price_minor: null,
    last_source_reference: null,
    last_observed_at: null,
    created_at: timestamp,
    triggered_at: null,
  };
}

function tradingPolicy() {
  return {
    workspace_id: workspaceId, execution_mode: "live", broker_connector_id: itemId,
    broker_account_verified: true, live_trading_enabled: false, kill_switch_active: true,
    owner_authorized_at: timestamp, session_valid_until: "2026-09-03T01:00:00Z",
    max_order_value_minor: 10_000, max_position_value_minor: 20_000,
    daily_loss_limit_minor: 5_000, per_symbol_exposure_limit_minor: 20_000,
    total_exposure_limit_minor: 50_000, max_open_orders: 3,
    allowed_instruments: ["AAPL"], allowed_venues: ["NASDAQ"], updated_at: timestamp,
  };
}

function normalizedQuote() {
  return {
    instrument_id: "global_stock:NASDAQ:AAPL", asset_class: "global_stock", symbol: "AAPL",
    exchange: "NASDAQ", currency: "USD", observed_at: timestamp, timezone: "UTC",
    last_price_minor: 10_000, bid_minor: 9_999, ask_minor: 10_001,
    open_minor: null, high_minor: null, low_minor: null, close_minor: null, volume: 100,
    provider: "owner-feed", source_reference: `connector-execution:${itemId}`,
    freshness_seconds: 1, data_quality: "fresh", connector_execution_id: itemId,
  };
}

function brokerOrder() {
  return {
    id: "ffffffff-ffff-4fff-8fff-ffffffffffff", workspace_id: workspaceId,
    broker_connector_id: itemId, asset_class: "global_stock", symbol: "AAPL",
    venue: "NASDAQ", currency: "USD", side: "buy", quantity_micros: 1_000_000,
    limit_price_minor: 10_000, notional_minor: 10_000, filled_quantity_micros: 500_000,
    client_order_key_sha256: "c".repeat(64), request_sha256: "d".repeat(64),
    provider_order_sha256: "e".repeat(64), status: "verified_open",
    submit_execution_id: itemId, status_execution_id: itemId,
    created_at: timestamp, verified_at: timestamp,
  };
}

describe("finance API client", () => {
  it("uses only owner-scoped paper endpoints and preserves source evidence", async () => {
    const responses = [
      { items: [workspace()] }, workspace(), workspace(), workspace(), workspace(),
      artifact(), artifact("backtest"), order(), { portfolio: artifact("portfolio"), risk: artifact("risk") },
      alert(), { items: [alert()] }, artifact("journal"),
    ];
    const fetchImplementation = vi.fn(async () => jsonResponse(responses.shift()));
    const client = new ApiClient(token, { fetchImplementation: fetchImplementation as typeof fetch });
    const paperRequest: PaperOrderRequest = {
      execution_mode: "paper", asset_class: "global_stock", symbol: "AAPL", side: "buy",
      quantity_micros: 1_000_000, price_minor: 10_000, observed_at: timestamp,
      source_reference: "owner.csv#row=2", owner_confirmed: true,
    };
    const quote = { asset_class: "global_stock" as const, symbol: "AAPL", price_minor: 10_100, observed_at: timestamp, source_reference: "owner.csv#row=3" };

    await client.listFinanceWorkspaces();
    await client.createFinanceWorkspace({ name: "Paper portfolio", base_currency: "USD", initial_cash_minor: 1_000_000, max_order_bps: 1_000, max_position_bps: 2_500 });
    await client.getFinanceWorkspace(workspaceId);
    await client.addMarketWatchItem(workspaceId, { asset_class: "global_stock", symbol: "AAPL", display_name: "Apple" });
    await client.removeMarketWatchItem(workspaceId, itemId);
    await client.runMarketResearch(workspaceId, { kind: "research", asset_class: "global_stock", subject: "AAPL", source_reference: "owner.csv", source_facts: [{ source_reference: "owner.csv", fact: "Owner fact" }] });
    await client.runMarketBacktest(workspaceId, { asset_class: "global_stock", symbol: "AAPL", source_reference: "owner.csv", bars: [{ observed_at: timestamp, close_minor: 100 }, { observed_at: "2026-09-04T00:00:00Z", close_minor: 110 }, { observed_at: "2026-09-05T00:00:00Z", close_minor: 120 }], fast_window: 2, slow_window: 3, initial_cash_minor: 100_000, fee_bps: 0 });
    await client.executePaperOrder(workspaceId, paperRequest);
    await client.analyzePaperPortfolio(workspaceId, { source_reference: "owner.csv", quotes: [quote] });
    await client.createMarketAlert(workspaceId, { asset_class: "global_stock", symbol: "AAPL", condition: "at_or_above", threshold_minor: 10_000 });
    await client.evaluateMarketAlerts(workspaceId, quote);
    await client.addTradingJournalEntry(workspaceId, { title: "Decision", note: "Paper decision only.", source_reference: "owner.csv" });

    const calls = fetchImplementation.mock.calls as unknown as Array<[
      URL | RequestInfo,
      RequestInit?,
    ]>;
    expect(calls.map((call) => `${call[1]?.method ?? "GET"} ${new URL(call[0].toString()).pathname}`)).toEqual([
      "GET /api/v1/finance/workspaces", "POST /api/v1/finance/workspaces",
      `GET /api/v1/finance/workspaces/${workspaceId}`,
      `POST /api/v1/finance/workspaces/${workspaceId}/watchlist`,
      `DELETE /api/v1/finance/workspaces/${workspaceId}/watchlist/${itemId}`,
      `POST /api/v1/finance/workspaces/${workspaceId}/research`,
      `POST /api/v1/finance/workspaces/${workspaceId}/backtests`,
      `POST /api/v1/finance/workspaces/${workspaceId}/paper-orders`,
      `POST /api/v1/finance/workspaces/${workspaceId}/portfolio-analysis`,
      `POST /api/v1/finance/workspaces/${workspaceId}/alerts`,
      `POST /api/v1/finance/workspaces/${workspaceId}/alerts/evaluate`,
      `POST /api/v1/finance/workspaces/${workspaceId}/journal`,
    ]);
    expect(JSON.parse(String(calls[7]?.[1]?.body))).toEqual(paperRequest);
    expect(JSON.parse(String(calls[10]?.[1]?.body))).toEqual({ quote });
    expect(JSON.stringify(calls)).not.toContain("live");
  });

  it("rejects a backend response that claims live broker capability", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => jsonResponse({ ...workspace(), execution_mode: "live", live_broker_status: "ready" })) as typeof fetch,
    });
    await expect(client.getFinanceWorkspace(workspaceId)).rejects.toMatchObject({ kind: "unexpected" });
  });

  it("uses dedicated fail-closed market and broker safety endpoints", async () => {
    const responses = [
      normalizedQuote(), tradingPolicy(), tradingPolicy(), tradingPolicy(), tradingPolicy(),
      { events: [], broker_orders: [brokerOrder()] }, brokerOrder(),
    ];
    const fetchImplementation = vi.fn(async () => jsonResponse(responses.shift()));
    const client = new ApiClient(token, { fetchImplementation: fetchImplementation as typeof fetch });
    const policyRequest = {
      broker_connector_id: itemId, account_path: "/broker/account", order_path: "/broker/orders",
      order_status_prefix: "/broker/orders/status/", max_order_value_minor: 10_000,
      max_position_value_minor: 20_000, daily_loss_limit_minor: 5_000,
      per_symbol_exposure_limit_minor: 20_000, total_exposure_limit_minor: 50_000,
      max_open_orders: 3, allowed_instruments: ["AAPL"], allowed_venues: ["NASDAQ"],
      owner_confirmation: "AUTHORIZE BROKER CONFIGURATION" as const,
    };
    const liveOrder = {
      execution_mode: "live" as const, asset_class: "global_stock" as const, symbol: "AAPL",
      venue: "NASDAQ", currency: "USD", side: "buy" as const, quantity_micros: 1_000_000,
      limit_price_minor: 10_000, client_order_key: "owner-order-00000001",
      market_data_connector_id: itemId, quote_path: "/quotes/AAPL", max_quote_age_seconds: 30,
    };

    await client.resolveMarketDataQuote({ connector_id: itemId, path: "/quotes/AAPL", max_age_seconds: 30 });
    await client.getTradingSafetyPolicy(workspaceId);
    await client.configureTradingSafetyPolicy(workspaceId, policyRequest);
    await client.setLiveTrading(workspaceId, { enabled: true, owner_confirmation: "ENABLE LIVE TRADING" });
    await client.activateTradingKillSwitch(workspaceId);
    await client.getTradingSafetyAudit(workspaceId);
    await client.placeVerifiedBrokerOrder(workspaceId, liveOrder);

    const calls = fetchImplementation.mock.calls as unknown as Array<[URL | RequestInfo, RequestInit?]>;
    expect(calls.map((call) => `${call[1]?.method ?? "GET"} ${new URL(call[0].toString()).pathname}`)).toEqual([
      "POST /api/v1/finance/market-data/quotes/resolve",
      `GET /api/v1/finance/workspaces/${workspaceId}/trading-safety`,
      `PUT /api/v1/finance/workspaces/${workspaceId}/trading-safety`,
      `POST /api/v1/finance/workspaces/${workspaceId}/trading-safety/live`,
      `POST /api/v1/finance/workspaces/${workspaceId}/trading-safety/kill-switch`,
      `GET /api/v1/finance/workspaces/${workspaceId}/trading-safety/audit`,
      `POST /api/v1/finance/workspaces/${workspaceId}/broker-orders`,
    ]);
    expect(JSON.parse(String(calls[2]?.[1]?.body))).toEqual(policyRequest);
    expect(JSON.parse(String(calls[6]?.[1]?.body))).toEqual(liveOrder);
  });
});
