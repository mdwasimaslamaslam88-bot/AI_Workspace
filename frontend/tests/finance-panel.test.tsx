import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FinanceWorkspace, PaperOrder, TradingSafetyPolicy } from "../src/api/contracts";
import { FinancePanel } from "../src/features/finance/FinancePanel";
import { rawSecret } from "./fixtures";

const workspaceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const timestamp = "2026-09-03T00:00:00Z";

function workspace(orders: PaperOrder[] = []): FinanceWorkspace {
  return {
    id: workspaceId, name: "Paper lab", base_currency: "USD",
    initial_cash_minor: 1_000_000, cash_minor: 900_000,
    max_order_bps: 1_000, max_position_bps: 2_500,
    created_at: timestamp, updated_at: timestamp,
    watch_items: [], positions: [], orders, alerts: [], artifacts: [],
    execution_mode: "paper", live_broker_status: "external_dependency",
  };
}

function paperOrder(): PaperOrder {
  return {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", asset_class: "global_stock",
    symbol: "AAPL", side: "buy", quantity_micros: 1_000_000, price_minor: 10_000,
    notional_minor: 10_000, source_reference: "owner.csv#row=2", observed_at: timestamp,
    status: "executed", rejection_code: null, cash_after_minor: 890_000,
    created_at: timestamp, execution_mode: "paper",
  };
}

function tradingPolicy(): TradingSafetyPolicy {
  return {
    workspace_id: workspaceId,
    execution_mode: "live",
    broker_connector_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    broker_account_verified: true,
    live_trading_enabled: false,
    kill_switch_active: true,
    owner_authorized_at: timestamp,
    session_valid_until: "2026-09-03T01:00:00Z",
    max_order_value_minor: 10_000,
    max_position_value_minor: 20_000,
    daily_loss_limit_minor: 5_000,
    per_symbol_exposure_limit_minor: 20_000,
    total_exposure_limit_minor: 50_000,
    max_open_orders: 3,
    allowed_instruments: ["AAPL"],
    allowed_venues: ["NASDAQ"],
    updated_at: timestamp,
  };
}

function props(values: FinanceWorkspace[] = [workspace()]) {
  return {
    onClose: vi.fn(), onLoad: vi.fn(async () => values),
    onGet: vi.fn(async () => workspace([paperOrder()])),
    onCreate: vi.fn(async () => workspace()), onAddWatch: vi.fn(async () => workspace()),
    onRemoveWatch: vi.fn(async () => workspace()),
    onResearch: vi.fn(async () => { throw new Error("unused"); }),
    onBacktest: vi.fn(async () => { throw new Error("unused"); }),
    onPaperOrder: vi.fn(async () => paperOrder()),
    onPortfolio: vi.fn(async () => { throw new Error("unused"); }),
    onCreateAlert: vi.fn(async () => { throw new Error("unused"); }),
    onEvaluateAlerts: vi.fn(async () => ({ items: [] })),
    onJournal: vi.fn(async () => { throw new Error("unused"); }),
    onGetTradingPolicy: vi.fn(async () => tradingPolicy()),
    onConfigureTradingPolicy: vi.fn(async () => { throw new Error("unused"); }),
    onSetLiveTrading: vi.fn(async () => { throw new Error("unused"); }),
    onKillSwitch: vi.fn(async () => tradingPolicy()),
    onGetTradingAudit: vi.fn(async () => ({ events: [], broker_orders: [] })),
  };
}

describe("FinancePanel", () => {
  it("creates only an explicitly bounded paper workspace", async () => {
    const actions = props([]);
    render(<FinancePanel {...actions} />);
    await screen.findByRole("button", { name: "Create paper workspace" });
    await userEvent.type(screen.getByLabelText("Name"), "Paper lab");
    await userEvent.clear(screen.getByLabelText("Base currency"));
    await userEvent.type(screen.getByLabelText("Base currency"), "USD");
    await userEvent.type(screen.getByLabelText("Initial cash, minor units"), "1000000");
    await userEvent.clear(screen.getByLabelText("Max order, basis points"));
    await userEvent.type(screen.getByLabelText("Max order, basis points"), "1000");
    await userEvent.clear(screen.getByLabelText("Max position, basis points"));
    await userEvent.type(screen.getByLabelText("Max position, basis points"), "2500");
    await userEvent.click(screen.getByRole("button", { name: "Create paper workspace" }));
    expect(actions.onCreate).toHaveBeenCalledWith({
      name: "Paper lab", base_currency: "USD", initial_cash_minor: 1_000_000,
      max_order_bps: 1_000, max_position_bps: 2_500,
    }, expect.any(AbortSignal));
  });

  it("sends the untouched, sourced order as paper-only after owner confirmation", async () => {
    const actions = props();
    render(<FinancePanel {...actions} />);
    await screen.findByText("Paper mode");
    const section = screen.getByText("Paper Trading Agent").closest("details");
    expect(section).not.toBeNull();
    await userEvent.click(screen.getByText("Paper Trading Agent"));
    const controls = within(section as HTMLElement);
    await userEvent.type(controls.getByLabelText("Symbol"), "AAPL");
    await userEvent.type(controls.getByLabelText("Quantity, micro-units"), "1000000");
    await userEvent.type(controls.getByLabelText("Observed price, minor units"), "10000");
    fireEvent.change(controls.getByLabelText("Observed at"), { target: { value: "2026-09-03T05:30" } });
    await userEvent.type(controls.getByLabelText("Quote source"), "owner.csv#row=2");
    await userEvent.click(controls.getByLabelText("Confirm this paper-only simulation"));
    await userEvent.click(controls.getByRole("button", { name: "Submit paper order" }));

    await waitFor(() => expect(actions.onPaperOrder).toHaveBeenCalled());
    expect(actions.onPaperOrder).toHaveBeenCalledWith(
      workspaceId,
      expect.objectContaining({
        execution_mode: "paper", symbol: "AAPL", quantity_micros: 1_000_000,
        price_minor: 10_000, source_reference: "owner.csv#row=2", owner_confirmed: true,
      }),
      expect.any(AbortSignal),
    );
    expect(screen.getByText(/executed · buy AAPL/)).toBeVisible();
  });

  it("redacts private backend failures", async () => {
    const actions = props([]);
    actions.onCreate.mockRejectedValueOnce(new Error(rawSecret));
    render(<FinancePanel {...actions} />);
    await userEvent.type(await screen.findByLabelText("Name"), "Paper lab");
    await userEvent.type(screen.getByLabelText("Initial cash, minor units"), "1000000");
    await userEvent.click(screen.getByRole("button", { name: "Create paper workspace" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rejected or its evidence could not be verified");
    expect(document.body.textContent).not.toContain(rawSecret);
  });

  it("shows the fail-closed broker policy and owner audit controls", async () => {
    const actions = props();
    render(<FinancePanel {...actions} />);

    await screen.findByText(/Mode: live · Kill switch: ACTIVE · Account: verified/);
    await userEvent.click(screen.getByText("Broker safety wall and audit"));
    expect(screen.getByText(/Safety events: 0 · Verified broker orders: 0/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Activate emergency kill switch" })).toBeDisabled();
    expect(screen.getByLabelText("Type AUTHORIZE BROKER CONFIGURATION")).toHaveAttribute(
      "pattern",
      "AUTHORIZE BROKER CONFIGURATION",
    );

    const confirmation = screen.getByLabelText("Type AUTHORIZE BROKER CONFIGURATION");
    fireEvent.change(confirmation, { target: { value: "yes" } });
    fireEvent.submit(confirmation.closest("form") as HTMLFormElement);
    expect(await screen.findByRole("alert")).toHaveTextContent("exact broker configuration confirmation");
    expect(actions.onConfigureTradingPolicy).not.toHaveBeenCalled();
  });
});
