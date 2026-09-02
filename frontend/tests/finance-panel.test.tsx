import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FinanceWorkspace, PaperOrder } from "../src/api/contracts";
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
});
