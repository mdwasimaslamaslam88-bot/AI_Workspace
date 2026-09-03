import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  BacktestRequest,
  FinanceArtifact,
  FinanceWorkspace,
  FinanceWorkspaceCreateRequest,
  MarketAlert,
  MarketAlertRequest,
  MarketAssetClass,
  MarketQuoteRequest,
  MarketResearchRequest,
  MarketWatchItemRequest,
  PaperOrder,
  PaperOrderRequest,
  PortfolioAnalysis,
  PortfolioAnalysisRequest,
  TradingJournalRequest,
  TradingSafetyAudit,
  TradingSafetyPolicy,
  TradingSafetyPolicyConfigureRequest,
  TradingSafetyToggleRequest,
} from "../../api/contracts";


interface FinancePanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<FinanceWorkspace[]>;
  onGet: (id: string, signal?: AbortSignal) => Promise<FinanceWorkspace>;
  onCreate: (request: FinanceWorkspaceCreateRequest, signal?: AbortSignal) => Promise<FinanceWorkspace>;
  onAddWatch: (id: string, request: MarketWatchItemRequest, signal?: AbortSignal) => Promise<FinanceWorkspace>;
  onRemoveWatch: (id: string, itemId: string, signal?: AbortSignal) => Promise<FinanceWorkspace>;
  onResearch: (id: string, request: MarketResearchRequest, signal?: AbortSignal) => Promise<FinanceArtifact>;
  onBacktest: (id: string, request: BacktestRequest, signal?: AbortSignal) => Promise<FinanceArtifact>;
  onPaperOrder: (id: string, request: PaperOrderRequest, signal?: AbortSignal) => Promise<PaperOrder>;
  onPortfolio: (id: string, request: PortfolioAnalysisRequest, signal?: AbortSignal) => Promise<PortfolioAnalysis>;
  onCreateAlert: (id: string, request: MarketAlertRequest, signal?: AbortSignal) => Promise<MarketAlert>;
  onEvaluateAlerts: (id: string, quote: MarketQuoteRequest, signal?: AbortSignal) => Promise<{ items: MarketAlert[] }>;
  onJournal: (id: string, request: TradingJournalRequest, signal?: AbortSignal) => Promise<FinanceArtifact>;
  onGetTradingPolicy: (id: string, signal?: AbortSignal) => Promise<TradingSafetyPolicy>;
  onConfigureTradingPolicy: (id: string, request: TradingSafetyPolicyConfigureRequest, signal?: AbortSignal) => Promise<TradingSafetyPolicy>;
  onSetLiveTrading: (id: string, request: TradingSafetyToggleRequest, signal?: AbortSignal) => Promise<TradingSafetyPolicy>;
  onKillSwitch: (id: string, signal?: AbortSignal) => Promise<TradingSafetyPolicy>;
  onGetTradingAudit: (id: string, signal?: AbortSignal) => Promise<TradingSafetyAudit>;
}

const ASSET_CLASSES: MarketAssetClass[] = ["indian_stock", "global_stock", "crypto", "fx"];

function replaceWorkspace(current: FinanceWorkspace[], value: FinanceWorkspace) {
  return [value, ...current.filter((workspace) => workspace.id !== value.id)].sort(
    (left, right) => right.created_at.localeCompare(left.created_at),
  );
}

function positiveInteger(value: FormDataEntryValue | null): number {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < 1 || result > 1_000_000_000_000_000) {
    throw new Error("A bounded positive integer is required.");
  }
  return result;
}

function parseBars(value: string): BacktestRequest["bars"] {
  const rows = value.split("\n").filter((row) => row.trim()).map((row) => {
    const [observedAt, rawClose, extra] = row.split(",").map((item) => item.trim());
    const close = Number(rawClose);
    if (extra !== undefined || !observedAt || !Number.isSafeInteger(close) || close < 1) {
      throw new Error("Each bar must be ISO timestamp,close_minor.");
    }
    return { observed_at: new Date(observedAt).toISOString(), close_minor: close };
  });
  if (rows.length < 3) throw new Error("At least three source bars are required.");
  return rows;
}

function parseQuotes(value: string): MarketQuoteRequest[] {
  return value.split("\n").filter((row) => row.trim()).map((row) => {
    const [assetClass, symbol, rawPrice, observedAt, source, extra] = row.split(",").map((item) => item.trim());
    const price = Number(rawPrice);
    if (
      extra !== undefined || !ASSET_CLASSES.includes(assetClass as MarketAssetClass) ||
      !symbol || !Number.isSafeInteger(price) || price < 1 || !observedAt || !source
    ) throw new Error("Each quote must be asset_class,symbol,price_minor,ISO timestamp,source.");
    return {
      asset_class: assetClass as MarketAssetClass,
      symbol,
      price_minor: price,
      observed_at: new Date(observedAt).toISOString(),
      source_reference: source,
    };
  });
}

export function FinancePanel({
  onClose, onLoad, onGet, onCreate, onAddWatch, onRemoveWatch, onResearch,
  onBacktest, onPaperOrder, onPortfolio, onCreateAlert, onEvaluateAlerts, onJournal,
  onGetTradingPolicy, onConfigureTradingPolicy, onSetLiveTrading, onKillSwitch,
  onGetTradingAudit,
}: FinancePanelProps) {
  const [workspaces, setWorkspaces] = useState<FinanceWorkspace[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [tradingPolicy, setTradingPolicy] = useState<TradingSafetyPolicy | null>(null);
  const [tradingAudit, setTradingAudit] = useState<TradingSafetyAudit>({ events: [], broker_orders: [] });
  const selected = useMemo(
    () => workspaces.find((workspace) => workspace.id === selectedId) ?? workspaces[0] ?? null,
    [selectedId, workspaces],
  );

  useEffect(() => {
    const controller = new AbortController();
    void onLoad(controller.signal).then((values) => {
      if (controller.signal.aborted) return;
      setWorkspaces(values);
      setSelectedId((current) => current ?? values[0]?.id ?? null);
    }).catch(() => {
      if (!controller.signal.aborted) setNotice("Finance workspace could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [onLoad]);

  useEffect(() => {
    if (selected === null) {
      setTradingPolicy(null);
      setTradingAudit({ events: [], broker_orders: [] });
      return;
    }
    const controller = new AbortController();
    void Promise.all([
      onGetTradingPolicy(selected.id, controller.signal).catch(() => null),
      onGetTradingAudit(selected.id, controller.signal).catch(() => ({ events: [], broker_orders: [] })),
    ]).then(([policy, audit]) => {
      if (!controller.signal.aborted) {
        setTradingPolicy(policy);
        setTradingAudit(audit);
      }
    });
    return () => controller.abort();
  }, [onGetTradingAudit, onGetTradingPolicy, selected]);

  async function perform<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> {
    if (busy) return;
    const controller = new AbortController();
    setBusy(true);
    setNotice(null);
    try {
      return await operation(controller.signal);
    } catch {
      setNotice("Finance action was rejected or its evidence could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh(id: string, signal?: AbortSignal) {
    const value = await onGet(id, signal);
    setWorkspaces((current) => replaceWorkspace(current, value));
    setSelectedId(id);
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const value = await perform((signal) => onCreate({
      name: String(values.get("name") ?? "").trim(),
      base_currency: String(values.get("base_currency") ?? "").trim(),
      initial_cash_minor: positiveInteger(values.get("initial_cash_minor")),
      max_order_bps: positiveInteger(values.get("max_order_bps")),
      max_position_bps: positiveInteger(values.get("max_position_bps")),
    }, signal));
    if (value !== undefined) {
      setWorkspaces((current) => replaceWorkspace(current, value));
      setSelectedId(value.id);
      form.reset();
    }
  }

  async function submitAndRefresh<T>(operation: (signal: AbortSignal) => Promise<T>) {
    if (selected === null) return;
    const value = await perform(operation);
    if (value !== undefined) await refresh(selected.id);
  }

  async function refreshTradingSafety(id: string) {
    const [policy, audit] = await Promise.all([
      onGetTradingPolicy(id),
      onGetTradingAudit(id),
    ]);
    setTradingPolicy(policy);
    setTradingAudit(audit);
  }

  return <aside className="workflow-panel finance-panel" aria-labelledby="finance-panel-title">
    <header className="workflow-panel-header">
      <div><p className="eyebrow">Finance intelligence</p><h2 id="finance-panel-title">Grounded paper market lab</h2></div>
      <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
    </header>
    <p className="field-help">All prices and history must include an owner-supplied source reference. Orders are paper-only. Live brokers remain disabled until separately authorized, risk-confirmed, and verified.</p>
    {notice !== null && <p className="notice notice-error" role="alert">{notice}</p>}
    <details open={workspaces.length === 0}>
      <summary>Create paper workspace</summary>
      <form className="workflow-form" onSubmit={(event) => void createWorkspace(event)}>
        <label>Name<input name="name" required maxLength={120} /></label>
        <label>Base currency<input name="base_currency" required defaultValue="USD" pattern="[A-Z]{3}" maxLength={3} /></label>
        <label>Initial cash, minor units<input name="initial_cash_minor" required type="number" min="1" step="1" /></label>
        <label>Max order, basis points<input name="max_order_bps" required type="number" min="1" max="10000" defaultValue="1000" /></label>
        <label>Max position, basis points<input name="max_position_bps" required type="number" min="1" max="10000" defaultValue="2500" /></label>
        <button disabled={busy} className="button button-primary">Create paper workspace</button>
      </form>
    </details>
    {loading ? <p role="status">Loading finance workspaces…</p> : workspaces.length > 0 && <label>Active workspace<select value={selected?.id ?? ""} onChange={(event) => setSelectedId(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}</select></label>}
    {selected !== null && <>
      <section className="finance-summary" aria-label="Paper portfolio status">
        <strong>{selected.name}</strong><span>Cash {selected.cash_minor} {selected.base_currency} minor units</span><span>Paper mode</span><span>Live broker: {tradingPolicy?.live_trading_enabled === true ? "explicitly enabled" : "blocked"}</span>
      </section>
      <details><summary>Broker safety wall and audit</summary>
        <p className="notice">Live trading is fail-closed. A healthy owner connector, verified account/MFA session, explicit limits, fresh attributed quote, exact confirmation, inactive kill switch, idempotency key, provider acknowledgement, and status readback are all mandatory.</p>
        <p role="status">Mode: {tradingPolicy?.execution_mode ?? "paper"} · Kill switch: {tradingPolicy?.kill_switch_active !== false ? "ACTIVE" : "inactive"} · Account: {tradingPolicy?.broker_account_verified === true ? "verified" : "not configured"}</p>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          const split = (name: string) => String(values.get(name) ?? "").split(",").map((value) => value.trim()).filter(Boolean);
          const ownerConfirmation = String(values.get("configuration_confirmation") ?? "").trim();
          if (ownerConfirmation !== "AUTHORIZE BROKER CONFIGURATION") {
            setNotice("The exact broker configuration confirmation is required.");
            return;
          }
          void perform((signal) => onConfigureTradingPolicy(selected.id, {
            broker_connector_id: String(values.get("broker_connector_id") ?? "").trim(),
            account_path: String(values.get("account_path") ?? "").trim(),
            order_path: String(values.get("order_path") ?? "").trim(),
            order_status_prefix: String(values.get("order_status_prefix") ?? "").trim(),
            max_order_value_minor: positiveInteger(values.get("max_order_value_minor")),
            max_position_value_minor: positiveInteger(values.get("max_position_value_minor")),
            daily_loss_limit_minor: positiveInteger(values.get("daily_loss_limit_minor")),
            per_symbol_exposure_limit_minor: positiveInteger(values.get("per_symbol_exposure_limit_minor")),
            total_exposure_limit_minor: positiveInteger(values.get("total_exposure_limit_minor")),
            max_open_orders: positiveInteger(values.get("max_open_orders")),
            allowed_instruments: split("allowed_instruments"),
            allowed_venues: split("allowed_venues"),
            owner_confirmation: ownerConfirmation,
          }, signal)).then((value) => {
            if (value !== undefined) void refreshTradingSafety(selected.id);
          });
        }}>
          <label>Owner broker connector ID<input name="broker_connector_id" required /></label>
          <label>Verified account path<input name="account_path" required placeholder="/broker/account" /></label>
          <label>Order path<input name="order_path" required placeholder="/broker/orders" /></label>
          <label>Order-status prefix<input name="order_status_prefix" required placeholder="/broker/orders/status/" /></label>
          <label>Max order value, minor units<input name="max_order_value_minor" type="number" min="1" required /></label>
          <label>Max position value<input name="max_position_value_minor" type="number" min="1" required /></label>
          <label>Daily loss limit<input name="daily_loss_limit_minor" type="number" min="1" required /></label>
          <label>Per-symbol exposure limit<input name="per_symbol_exposure_limit_minor" type="number" min="1" required /></label>
          <label>Total exposure limit<input name="total_exposure_limit_minor" type="number" min="1" required /></label>
          <label>Max open orders<input name="max_open_orders" type="number" min="1" max="1000" required /></label>
          <label>Allowed instruments, comma-separated<input name="allowed_instruments" required placeholder="ACME" /></label>
          <label>Allowed venues, comma-separated<input name="allowed_venues" required placeholder="NASDAQ" /></label>
          <label>Type AUTHORIZE BROKER CONFIGURATION<input name="configuration_confirmation" required pattern="AUTHORIZE BROKER CONFIGURATION" /></label>
          <button disabled={busy} className="button button-primary">Verify and save disabled policy</button>
        </form>
        {tradingPolicy !== null && <>
          <form className="workflow-form" onSubmit={(event) => {
            event.preventDefault(); const values = new FormData(event.currentTarget);
            const enabled = !tradingPolicy.live_trading_enabled;
            void perform((signal) => onSetLiveTrading(selected.id, {
              enabled,
              owner_confirmation: String(values.get("owner_confirmation") ?? "").trim(),
            }, signal)).then((value) => {
              if (value !== undefined) void refreshTradingSafety(selected.id);
            });
          }}>
            <label>{tradingPolicy.live_trading_enabled ? "Type DISABLE LIVE TRADING" : "Type ENABLE LIVE TRADING"}<input name="owner_confirmation" required /></label>
            <button disabled={busy} className="button button-primary">{tradingPolicy.live_trading_enabled ? "Disable live trading" : "Enable live trading after re-verification"}</button>
          </form>
          <button type="button" disabled={busy || tradingPolicy.kill_switch_active} className="button button-quiet" onClick={() => {
            void perform((signal) => onKillSwitch(selected.id, signal)).then((value) => {
              if (value !== undefined) void refreshTradingSafety(selected.id);
            });
          }}>Activate emergency kill switch</button>
        </>}
        <p>Safety events: {tradingAudit.events.length} · Verified broker orders: {tradingAudit.broker_orders.length}</p>
        <ol>{tradingAudit.events.map((event) => <li key={event.id}>{event.action} · policy {event.policy_sha256.slice(0, 12)}…</li>)}</ol>
      </details>
      <details><summary>Watchlist ({selected.watch_items.length})</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onAddWatch(selected.id, {
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            symbol: String(values.get("symbol") ?? "").trim(),
            display_name: String(values.get("display_name") ?? "").trim(),
          }, signal));
        }}><AssetClassSelect /><label>Symbol<input name="symbol" required pattern="[A-Z0-9][A-Z0-9._:/-]{0,23}" /></label><label>Display name<input name="display_name" required maxLength={120} /></label><button disabled={busy} className="button button-primary">Add watch item</button></form>
        <ul>{selected.watch_items.map((item) => <li key={item.id}>{item.asset_class} · {item.symbol} · {item.display_name} <button type="button" className="button button-quiet" disabled={busy} onClick={() => void submitAndRefresh((signal) => onRemoveWatch(selected.id, item.id, signal))}>Remove</button></li>)}</ul>
      </details>
      <details><summary>Market Research / Strategy Agent</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onResearch(selected.id, {
            kind: String(values.get("kind")) as "research" | "strategy",
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            subject: String(values.get("subject") ?? "").trim(),
            source_reference: String(values.get("source_reference") ?? "").trim(),
            source_facts: [{ source_reference: String(values.get("fact_source") ?? "").trim(), fact: String(values.get("fact") ?? "").trim() }],
          }, signal));
        }}><label>Artifact<select name="kind"><option value="research">Research</option><option value="strategy">Paper strategy</option></select></label><AssetClassSelect /><label>Subject<input name="subject" required maxLength={500} /></label><label>Dataset/report reference<input name="source_reference" required maxLength={512} /></label><label>Fact source<input name="fact_source" required maxLength={512} /></label><label>Supplied fact<textarea name="fact" required maxLength={2_000} /></label><button disabled={busy} className="button button-primary">Generate verified artifact</button></form>
      </details>
      <details><summary>Backtesting Agent</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onBacktest(selected.id, {
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            symbol: String(values.get("symbol") ?? "").trim(),
            source_reference: String(values.get("source_reference") ?? "").trim(),
            bars: parseBars(String(values.get("bars") ?? "")),
            fast_window: positiveInteger(values.get("fast_window")),
            slow_window: positiveInteger(values.get("slow_window")),
            initial_cash_minor: positiveInteger(values.get("initial_cash_minor")),
            fee_bps: Number(values.get("fee_bps")),
            slippage_bps: Number(values.get("slippage_bps")),
            position_size_bps: positiveInteger(values.get("position_size_bps")),
            stop_loss_bps: values.get("stop_loss_bps") ? positiveInteger(values.get("stop_loss_bps")) : null,
            take_profit_bps: values.get("take_profit_bps") ? positiveInteger(values.get("take_profit_bps")) : null,
          }, signal));
        }}><AssetClassSelect /><label>Symbol<input name="symbol" required /></label><label>History source<input name="source_reference" required /></label><label>Bars: ISO timestamp,close_minor<textarea name="bars" required rows={5} /></label><label>Fast window<input name="fast_window" type="number" min="2" defaultValue="2" required /></label><label>Slow window<input name="slow_window" type="number" min="3" defaultValue="3" required /></label><label>Initial cash<input name="initial_cash_minor" type="number" min="1" required /></label><label>Fee bps<input name="fee_bps" type="number" min="0" max="1000" defaultValue="0" required /></label><label>Slippage bps<input name="slippage_bps" type="number" min="0" max="1000" defaultValue="0" required /></label><label>Position size bps<input name="position_size_bps" type="number" min="1" max="10000" defaultValue="10000" required /></label><label>Stop loss bps (optional)<input name="stop_loss_bps" type="number" min="1" max="10000" /></label><label>Take profit bps (optional)<input name="take_profit_bps" type="number" min="1" max="100000" /></label><button disabled={busy} className="button button-primary">Run sourced backtest</button></form>
      </details>
      <details><summary>Paper Trading Agent</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onPaperOrder(selected.id, {
            execution_mode: "paper",
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            symbol: String(values.get("symbol") ?? "").trim(),
            side: String(values.get("side")) as "buy" | "sell",
            quantity_micros: positiveInteger(values.get("quantity_micros")),
            price_minor: positiveInteger(values.get("price_minor")),
            observed_at: new Date(String(values.get("observed_at"))).toISOString(),
            source_reference: String(values.get("source_reference") ?? "").trim(),
            owner_confirmed: values.get("owner_confirmed") === "on",
          }, signal));
        }}><p className="notice">Simulation only. No broker or live order endpoint is used.</p><AssetClassSelect /><label>Symbol<input name="symbol" required /></label><label>Side<select name="side"><option value="buy">Buy</option><option value="sell">Sell</option></select></label><label>Quantity, micro-units<input name="quantity_micros" type="number" min="1" required /></label><label>Observed price, minor units<input name="price_minor" type="number" min="1" required /></label><label>Observed at<input name="observed_at" type="datetime-local" required /></label><label>Quote source<input name="source_reference" required maxLength={512} /></label><label><input name="owner_confirmed" type="checkbox" required /> Confirm this paper-only simulation</label><button disabled={busy} className="button button-primary">Submit paper order</button></form>
        <ol>{selected.orders.map((order) => <li key={order.id}>{order.status} · {order.side} {order.symbol} · {order.notional_minor} minor units{order.rejection_code !== null ? ` · ${order.rejection_code}` : ""}</li>)}</ol>
      </details>
      <details><summary>Portfolio / Risk Agents</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onPortfolio(selected.id, {
            source_reference: String(values.get("source_reference") ?? "").trim(),
            quotes: parseQuotes(String(values.get("quotes") ?? "")),
          }, signal));
        }}><label>Valuation dataset<input name="source_reference" required /></label><label>Quotes: asset_class,symbol,price_minor,ISO timestamp,source<textarea name="quotes" rows={4} required={selected.positions.length > 0} /></label><button disabled={busy} className="button button-primary">Analyze sourced portfolio</button></form>
      </details>
      <details><summary>Alert Agent</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onCreateAlert(selected.id, {
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            symbol: String(values.get("symbol") ?? "").trim(),
            condition: String(values.get("condition")) as "at_or_above" | "at_or_below",
            threshold_minor: positiveInteger(values.get("threshold_minor")),
          }, signal));
        }}><AssetClassSelect /><label>Symbol<input name="symbol" required /></label><label>Condition<select name="condition"><option value="at_or_above">At or above</option><option value="at_or_below">At or below</option></select></label><label>Threshold minor units<input name="threshold_minor" type="number" min="1" required /></label><button disabled={busy} className="button button-primary">Create alert</button></form>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onEvaluateAlerts(selected.id, {
            asset_class: String(values.get("asset_class")) as MarketAssetClass,
            symbol: String(values.get("symbol") ?? "").trim(),
            price_minor: positiveInteger(values.get("price_minor")),
            observed_at: new Date(String(values.get("observed_at"))).toISOString(),
            source_reference: String(values.get("source_reference") ?? "").trim(),
          }, signal));
        }}><AssetClassSelect /><label>Symbol<input name="symbol" required /></label><label>Observed price<input name="price_minor" type="number" min="1" required /></label><label>Observed at<input name="observed_at" type="datetime-local" required /></label><label>Quote source<input name="source_reference" required /></label><button disabled={busy} className="button button-secondary">Evaluate from source quote</button></form>
        <ul>{selected.alerts.map((alert) => <li key={alert.id}>{alert.symbol} {alert.condition} {alert.threshold_minor} · {alert.status}</li>)}</ul>
      </details>
      <details><summary>Trading Journal Agent</summary>
        <form className="workflow-form" onSubmit={(event) => {
          event.preventDefault(); const values = new FormData(event.currentTarget);
          void submitAndRefresh((signal) => onJournal(selected.id, {
            title: String(values.get("title") ?? "").trim(),
            note: String(values.get("note") ?? "").trim(),
            source_reference: String(values.get("source_reference") ?? "").trim(),
          }, signal));
        }}><label>Title<input name="title" required maxLength={160} /></label><label>Note<textarea name="note" required maxLength={20_000} /></label><label>Related source<input name="source_reference" required maxLength={512} /></label><button disabled={busy} className="button button-primary">Save journal entry</button></form>
      </details>
      <details><summary>Verified artifacts ({selected.artifacts.length})</summary><ol>{selected.artifacts.map((artifact) => <li key={artifact.id}><strong>{artifact.kind} · {artifact.title}</strong><small>{artifact.model_id} · {artifact.source_reference}</small><pre>{artifact.output}</pre></li>)}</ol></details>
    </>}
  </aside>;
}

function AssetClassSelect() {
  return <label>Asset class<select name="asset_class">{ASSET_CLASSES.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>;
}
