import type { PaperOrderRequest } from "@work-station/shared";
import { describe, expect, it, vi } from "vitest";

import { MobileApiClient } from "../src/api/client";

const workspaceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const timestamp = "2026-09-03T00:00:00Z";

function workspace() {
  return {
    id: workspaceId, name: "Mobile paper lab", base_currency: "USD",
    initial_cash_minor: 1_000_000, cash_minor: 1_000_000,
    max_order_bps: 1_000, max_position_bps: 2_500,
    created_at: timestamp, updated_at: timestamp,
    watch_items: [], positions: [], orders: [], alerts: [], artifacts: [],
    execution_mode: "paper", live_broker_status: "external_dependency",
  };
}

function order() {
  return {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", asset_class: "crypto", symbol: "BTC/USD",
    side: "buy", quantity_micros: 1_000, price_minor: 10_000, notional_minor: 10,
    source_reference: "owner-feed#1", observed_at: timestamp, status: "executed", rejection_code: null,
    cash_after_minor: 999_990, created_at: timestamp, execution_mode: "paper",
  };
}

function tradingPolicy() {
  return {
    workspace_id: workspaceId, execution_mode: "live",
    broker_connector_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    broker_account_verified: true, live_trading_enabled: false, kill_switch_active: true,
    owner_authorized_at: timestamp, session_valid_until: "2026-09-03T01:00:00Z",
    max_order_value_minor: 10_000, max_position_value_minor: 20_000,
    daily_loss_limit_minor: 5_000, per_symbol_exposure_limit_minor: 20_000,
    total_exposure_limit_minor: 50_000, max_open_orders: 3,
    allowed_instruments: ["BTC/USD"], allowed_venues: ["OWNERX"], updated_at: timestamp,
  };
}

describe("mobile finance API", () => {
  it("keeps finance identity in paths and confirmed paper evidence in the body", async () => {
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const responseQueue: unknown[] = [{ items: [workspace()] }, workspace(), order(), workspace()];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      calls.push({
        path: new URL(input.toString()).pathname,
        method: init?.method ?? "GET",
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      return new Response(JSON.stringify(responseQueue.shift()), { status: 200 });
    });
    const client = new MobileApiClient("private-mobile-token", {
      baseUrl: "https://work-station.example.ts.net", fetchImplementation: fetchMock,
    });
    const request: PaperOrderRequest = {
      execution_mode: "paper", asset_class: "crypto", symbol: "BTC/USD", side: "buy",
      quantity_micros: 1_000, price_minor: 10_000, observed_at: timestamp,
      source_reference: "owner-feed#1", owner_confirmed: true,
    };

    await client.listFinanceWorkspaces();
    await client.createFinanceWorkspace({ name: "Mobile paper lab", base_currency: "USD", initial_cash_minor: 1_000_000, max_order_bps: 1_000, max_position_bps: 2_500 });
    await client.executePaperOrder(workspaceId, request);
    await client.getFinanceWorkspace(workspaceId);

    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/v1/finance/workspaces",
      "POST /api/v1/finance/workspaces",
      `POST /api/v1/finance/workspaces/${workspaceId}/paper-orders`,
      `GET /api/v1/finance/workspaces/${workspaceId}`,
    ]);
    expect(calls[2]?.body).toEqual(request);
    expect(JSON.stringify(calls)).not.toContain("private-mobile-token");
    expect(JSON.stringify(calls)).not.toContain("broker");
  });

  it("exposes explicit mobile safety controls without enabling live trading by default", async () => {
    const connectorId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const responseQueue: unknown[] = [tradingPolicy(), tradingPolicy(), tradingPolicy(), { events: [], broker_orders: [] }];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      calls.push({
        path: new URL(input.toString()).pathname,
        method: init?.method ?? "GET",
        body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      });
      return new Response(JSON.stringify(responseQueue.shift()), { status: 200 });
    });
    const client = new MobileApiClient("private-mobile-token", {
      baseUrl: "https://work-station.example.ts.net", fetchImplementation: fetchMock,
    });
    const policyRequest = {
      broker_connector_id: connectorId, account_path: "/broker/account", order_path: "/broker/orders",
      order_status_prefix: "/broker/orders/status/", max_order_value_minor: 10_000,
      max_position_value_minor: 20_000, daily_loss_limit_minor: 5_000,
      per_symbol_exposure_limit_minor: 20_000, total_exposure_limit_minor: 50_000,
      max_open_orders: 3, allowed_instruments: ["BTC/USD"], allowed_venues: ["OWNERX"],
      owner_confirmation: "AUTHORIZE BROKER CONFIGURATION" as const,
    };

    await client.getTradingSafetyPolicy(workspaceId);
    await client.configureTradingSafetyPolicy(workspaceId, policyRequest);
    await client.activateTradingKillSwitch(workspaceId);
    await client.getTradingSafetyAudit(workspaceId);

    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `GET /api/v1/finance/workspaces/${workspaceId}/trading-safety`,
      `PUT /api/v1/finance/workspaces/${workspaceId}/trading-safety`,
      `POST /api/v1/finance/workspaces/${workspaceId}/trading-safety/kill-switch`,
      `GET /api/v1/finance/workspaces/${workspaceId}/trading-safety/audit`,
    ]);
    expect(calls[1]?.body).toEqual(policyRequest);
    expect(JSON.stringify(calls)).not.toContain("private-mobile-token");
    expect(responseQueue).toHaveLength(0);
  });
});
