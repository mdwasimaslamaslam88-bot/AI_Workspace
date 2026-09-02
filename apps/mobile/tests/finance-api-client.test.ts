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
});
