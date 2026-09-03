import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { isMarketingPublisherConnector } from "../../api/contracts";
import type {
  Connector,
  MarketingAnalyticsRequest,
  MarketingCampaign,
  MarketingCampaignCreateRequest,
  MarketingChannel,
} from "../../api/contracts";


interface MarketingPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<MarketingCampaign[]>;
  onLoadConnectors: (signal?: AbortSignal) => Promise<Connector[]>;
  onCreate: (request: MarketingCampaignCreateRequest, signal?: AbortSignal) => Promise<MarketingCampaign>;
  onGet: (id: string, signal?: AbortSignal) => Promise<MarketingCampaign>;
  onStart: (id: string, signal?: AbortSignal) => Promise<MarketingCampaign>;
  onApprove: (id: string, signal?: AbortSignal) => Promise<MarketingCampaign>;
  onAnalytics: (id: string, request: MarketingAnalyticsRequest, signal?: AbortSignal) => Promise<MarketingCampaign>;
  onCancel: (id: string, signal?: AbortSignal) => Promise<MarketingCampaign>;
}

const CHANNELS: MarketingChannel[] = ["email", "social", "search", "web"];
const POLLING = new Set(["pending", "running", "publishing"]);
const CANCELLABLE = new Set(["pending", "running", "needs_approval", "awaiting_analytics"]);

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("cancelled", "AbortError"));
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("cancelled", "AbortError"));
    }, { once: true });
  });
}

function replaceCampaign(current: MarketingCampaign[], updated: MarketingCampaign) {
  return [updated, ...current.filter((item) => item.id !== updated.id)].sort(
    (left, right) => right.created_at.localeCompare(left.created_at),
  );
}

export function MarketingPanel({
  onClose,
  onLoad,
  onLoadConnectors,
  onCreate,
  onGet,
  onStart,
  onApprove,
  onAnalytics,
  onCancel,
}: MarketingPanelProps) {
  const [campaigns, setCampaigns] = useState<MarketingCampaign[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [product, setProduct] = useState("");
  const [audience, setAudience] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [sourceFact, setSourceFact] = useState("");
  const [channels, setChannels] = useState<MarketingChannel[]>(["email"]);
  const [publisherId, setPublisherId] = useState("");
  const [publishPath, setPublishPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const controllers = useRef(new Map<string, AbortController>());

  const poll = useCallback(async (id: string, controller: AbortController) => {
    try {
      for (let attempt = 0; attempt < 1_250; attempt += 1) {
        const campaign = await onGet(id, controller.signal);
        if (controller.signal.aborted) return;
        setCampaigns((current) => replaceCampaign(current, campaign));
        if (!POLLING.has(campaign.status)) return;
        await delay(500, controller.signal);
      }
      if (!controller.signal.aborted) setNotice("Campaign state exceeded its server deadline.");
    } catch {
      if (!controller.signal.aborted) setNotice("Campaign state could not be refreshed.");
    } finally {
      controllers.current.delete(id);
    }
  }, [onGet]);

  const beginPolling = useCallback((id: string) => {
    controllers.current.get(id)?.abort();
    const controller = new AbortController();
    controllers.current.set(id, controller);
    void poll(id, controller);
  }, [poll]);

  useEffect(() => {
    const controller = new AbortController();
    const activeControllers = controllers.current;
    void Promise.all([onLoad(controller.signal), onLoadConnectors(controller.signal)])
      .then(([items, availableConnectors]) => {
        if (controller.signal.aborted) return;
        setCampaigns(items);
        setConnectors(availableConnectors.filter(isMarketingPublisherConnector));
        for (const campaign of items.filter((item) => POLLING.has(item.status))) {
          beginPolling(campaign.id);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("Marketing workspace could not be loaded.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      for (const active of activeControllers.values()) active.abort();
      activeControllers.clear();
    };
  }, [beginPolling, onLoad, onLoadConnectors]);

  async function perform(operation: (signal: AbortSignal) => Promise<MarketingCampaign>) {
    if (busy) return;
    const controller = new AbortController();
    setBusy(true);
    setNotice(null);
    try {
      const campaign = await operation(controller.signal);
      setCampaigns((current) => replaceCampaign(current, campaign));
      return campaign;
    } catch {
      setNotice("The campaign action was rejected or could not be verified.");
    } finally {
      setBusy(false);
    }
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const request: MarketingCampaignCreateRequest = {
      name: name.trim(),
      objective: objective.trim(),
      product: product.trim(),
      audience: audience.trim(),
      channels,
      source_facts: [{ source_reference: sourceReference.trim(), fact: sourceFact.trim() }],
    };
    if (publisherId && publishPath.trim()) {
      request.publisher_connector_id = publisherId;
      request.publish_path = publishPath.trim();
    }
    const created = await perform((signal) => onCreate(request, signal));
    if (created !== undefined) {
      setName("");
      setObjective("");
      setProduct("");
      setAudience("");
      setSourceReference("");
      setSourceFact("");
    }
  }

  async function start(id: string) {
    const campaign = await perform((signal) => onStart(id, signal));
    if (campaign !== undefined) beginPolling(id);
  }

  async function approve(id: string) {
    const campaign = await perform((signal) => onApprove(id, signal));
    if (campaign?.status === "publishing") beginPolling(id);
  }

  async function analytics(id: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const request: MarketingAnalyticsRequest = {
      source_reference: String(values.get("source_reference") ?? "").trim(),
      observed_at: new Date().toISOString(),
      impressions: Number(values.get("impressions")),
      clicks: Number(values.get("clicks")),
      conversions: Number(values.get("conversions")),
      spend_minor: Number(values.get("spend_minor")),
      revenue_minor: Number(values.get("revenue_minor")),
    };
    await perform((signal) => onAnalytics(id, request, signal));
  }

  const validDraft = [name, objective, product, audience, sourceReference, sourceFact]
    .every((value) => value.trim().length > 0) && channels.length > 0 &&
    ((publisherId.length === 0 && publishPath.trim().length === 0) ||
      (publisherId.length > 0 && publishPath.trim().length > 0));

  return (
    <aside className="workflow-panel marketing-panel" aria-labelledby="marketing-panel-title">
      <header className="workflow-panel-header">
        <div><p className="eyebrow">Business workspace</p><h2 id="marketing-panel-title">Verified campaigns</h2></div>
        <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
      </header>
      <p className="field-help">Research, strategy, content, and creative use verified local agents. Publishing requires an owner-approved, healthy connector advertising campaign.publish; analytics use only submitted source data.</p>
      <form className="workflow-form" onSubmit={(event) => void create(event)}>
        <label>Campaign name<input maxLength={120} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Objective<textarea maxLength={2_000} rows={2} value={objective} onChange={(event) => setObjective(event.target.value)} /></label>
        <label>Product<input maxLength={500} value={product} onChange={(event) => setProduct(event.target.value)} /></label>
        <label>Audience<input maxLength={1_000} value={audience} onChange={(event) => setAudience(event.target.value)} /></label>
        <fieldset><legend>Channels</legend>{CHANNELS.map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} onChange={(event) => setChannels((current) => event.target.checked ? [...current, channel] : current.filter((item) => item !== channel))} /> {channel}</label>)}</fieldset>
        <label>Source reference<input maxLength={512} value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} placeholder="brief.md#section" /></label>
        <label>Source fact<textarea maxLength={2_000} rows={2} value={sourceFact} onChange={(event) => setSourceFact(event.target.value)} /></label>
        <label>Publisher (optional)<select value={publisherId} onChange={(event) => setPublisherId(event.target.value)}><option value="">Draft only — external boundary</option>{connectors.map((connector) => <option key={connector.id} value={connector.id}>{connector.name}</option>)}</select></label>
        <label>Authorized publish path (optional)<input maxLength={512} value={publishPath} onChange={(event) => setPublishPath(event.target.value)} placeholder="/v1/campaigns" /></label>
        <button className="button button-primary" disabled={busy || !validDraft}>{busy ? "Working…" : "Create campaign"}</button>
      </form>
      {notice !== null && <p className="notice notice-error" role="alert">{notice}</p>}
      <h3>Campaign history</h3>
      {loading ? <p role="status" className="muted">Loading campaigns…</p> : <ol className="workflow-history marketing-history">
        {campaigns.length === 0 && <li className="muted">No campaigns yet.</li>}
        {campaigns.map((campaign) => {
          const completed = campaign.stages.filter((stage) => stage.status === "completed").length;
          return <li key={campaign.id}>
            <div className="workflow-heading"><strong>{campaign.name}</strong><span className={`workflow-status workflow-status-${campaign.status}`}>{campaign.status.replaceAll("_", " ")}</span></div>
            <p>{campaign.product} · {campaign.channels.join(", ")}</p>
            <progress value={completed} max={campaign.stages.length}>{completed} of {campaign.stages.length}</progress>
            <ol className="workflow-steps" aria-label={`${campaign.name} stage activity`}>{campaign.stages.map((stage) => <li key={stage.id}><div><strong>{stage.kind}</strong><span>{stage.status}</span></div>{stage.model_id !== null && <small>{stage.model_id}</small>}{stage.output !== null && <details><summary>Verified output</summary><pre>{stage.output}</pre></details>}{stage.error_code !== null && <small>Safe failure: {stage.error_code}</small>}</li>)}</ol>
            <div className="button-row">
              {campaign.status === "pending" && <button type="button" className="button button-primary" disabled={busy} onClick={() => void start(campaign.id)}>Start</button>}
              {campaign.status === "needs_approval" && <button type="button" className="button button-primary" disabled={busy || campaign.publisher_connector_id === null} onClick={() => void approve(campaign.id)}>{campaign.publisher_connector_id === null ? "Publisher required" : "Approve & publish"}</button>}
              {CANCELLABLE.has(campaign.status) && <button type="button" className="button button-quiet" disabled={busy} onClick={() => void perform((signal) => onCancel(campaign.id, signal))}>Cancel</button>}
            </div>
            {campaign.status === "awaiting_analytics" && <form className="marketing-analytics" onSubmit={(event) => void analytics(campaign.id, event)}><label>Analytics source<input name="source_reference" required maxLength={512} /></label>{["impressions", "clicks", "conversions", "spend_minor", "revenue_minor"].map((field) => <label key={field}>{field.replaceAll("_", " ")}<input name={field} type="number" min="0" step="1" required /></label>)}<button className="button button-primary" disabled={busy}>Submit source analytics</button></form>}
            {campaign.analytics !== null && <details><summary>Grounded analytics</summary><pre>{JSON.stringify(campaign.analytics, null, 2)}</pre></details>}
          </li>;
        })}
      </ol>}
    </aside>
  );
}
