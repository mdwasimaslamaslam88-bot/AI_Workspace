import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import type {
  CommunicationAccepted,
  CommunicationCapabilities,
  CommunicationRequest,
  Connector,
} from "../../api/contracts";


interface CommunicationPanelProps {
  onClose: () => void;
  onConfigure: () => void;
  onLoadCapabilities: (signal?: AbortSignal) => Promise<CommunicationCapabilities>;
  onLoadConnectors: (signal?: AbortSignal) => Promise<Connector[]>;
  onStartPhoneCall: (
    request: CommunicationRequest,
    signal?: AbortSignal,
  ) => Promise<CommunicationAccepted>;
  onScheduleCallback: (
    request: CommunicationRequest,
    signal?: AbortSignal,
  ) => Promise<CommunicationAccepted>;
}

type Operation = "phone_call" | "callback";

export function CommunicationPanel({
  onClose,
  onConfigure,
  onLoadCapabilities,
  onLoadConnectors,
  onStartPhoneCall,
  onScheduleCallback,
}: CommunicationPanelProps) {
  const [capabilities, setCapabilities] = useState<CommunicationCapabilities | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [operation, setOperation] = useState<Operation>("phone_call");
  const [connectorId, setConnectorId] = useState("");
  const [destination, setDestination] = useState("");
  const [purpose, setPurpose] = useState("");
  const [approved, setApproved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<CommunicationAccepted | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [nextCapabilities, connectorPage] = await Promise.all([
        onLoadCapabilities(signal),
        onLoadConnectors(signal),
      ]);
      if (signal?.aborted) return;
      setCapabilities(nextCapabilities);
      setConnectors(connectorPage);
      setNotice(null);
    } catch {
      if (!signal?.aborted) setNotice("Communication status could not be loaded.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [onLoadCapabilities, onLoadConnectors]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const capability = capabilities?.[operation] ?? null;
  const eligibleConnectors = useMemo(() => {
    const eligibleIds = new Set(capability?.connector_ids ?? []);
    return connectors.filter((connector) => eligibleIds.has(connector.id));
  }, [capability?.connector_ids, connectors]);

  useEffect(() => {
    setConnectorId((current) =>
      eligibleConnectors.some((connector) => connector.id === current)
        ? current
        : eligibleConnectors[0]?.id ?? "",
    );
    setReceipt(null);
  }, [eligibleConnectors]);

  const canSubmit = Boolean(
    !loading && !busy && capability?.configured &&
    connectorId &&
    /^\+[1-9][0-9]{7,14}$/.test(destination) &&
    purpose.trim().length > 0 && purpose.trim().length <= 240 && approved,
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    const controller = new AbortController();
    setBusy(true);
    setNotice(null);
    setReceipt(null);
    const request: CommunicationRequest = {
      destination,
      purpose: purpose.trim(),
      owner_approved: true,
      connector_id: connectorId,
    };
    try {
      const accepted = operation === "phone_call"
        ? await onStartPhoneCall(request, controller.signal)
        : await onScheduleCallback(request, controller.signal);
      setReceipt(accepted);
      setNotice("The provider returned a matching acceptance receipt and the connector execution was audited.");
      setApproved(false);
    } catch {
      setNotice("The provider did not return a verified acceptance receipt. No success was claimed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="workflow-panel communication-panel" aria-labelledby="communication-panel-title">
      <header className="workflow-panel-header">
        <div>
          <p className="eyebrow">Global communications</p>
          <h2 id="communication-panel-title">Calls & callbacks</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
      </header>
      <p className="field-help">
        A request is shown as accepted only after an owner-scoped provider returns a matching receipt.
        Carrier service, phone numbers, billing, and provider authentication remain external.
      </p>
      {loading ? <p className="muted" role="status">Checking communication providers…</p> : (
        <form className="workflow-form" onSubmit={(event) => void submit(event)}>
          <label>
            Action
            <select value={operation} onChange={(event) => setOperation(event.target.value as Operation)}>
              <option value="phone_call">Place phone call</option>
              <option value="callback">Schedule callback</option>
            </select>
          </label>
          <label>
            Verified communication connector
            <select value={connectorId} onChange={(event) => setConnectorId(event.target.value)}>
              <option value="">No healthy eligible connector</option>
              {eligibleConnectors.map((connector) => (
                <option key={connector.id} value={connector.id}>
                  {connector.name} · {connector.provider}
                </option>
              ))}
            </select>
          </label>
          <label>
            Destination (E.164)
            <input
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              maxLength={16}
              placeholder="+14155550123"
              value={destination}
              onChange={(event) => setDestination(event.target.value)}
            />
          </label>
          <label>
            Purpose
            <textarea
              rows={3}
              maxLength={240}
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={approved}
              onChange={(event) => setApproved(event.target.checked)}
            />
            I approve this external communication and its provider charges.
          </label>
          <button className="button button-primary" disabled={!canSubmit}>
            {busy ? "Waiting for provider…" : operation === "phone_call" ? "Place verified call" : "Schedule verified callback"}
          </button>
        </form>
      )}
      {!loading && capability?.configured !== true && (
        <div className="notice" role="status">
          <p>No health-verified communication provider is configured.</p>
          <button type="button" className="button button-secondary" onClick={onConfigure}>
            Configure communication gateway
          </button>
        </div>
      )}
      {notice !== null && <p className="notice" role="status">{notice}</p>}
      {receipt !== null && (
        <dl className="connector-result">
          <div><dt>Provider state</dt><dd>{receipt.state.replaceAll("_", " ")}</dd></div>
          <div><dt>Request</dt><dd><code>{receipt.request_id}</code></dd></div>
          <div><dt>Audit execution</dt><dd><code>{receipt.connector_execution_id}</code></dd></div>
        </dl>
      )}
      <p className="field-help">
        Video calling and live screen sharing require a separately verified WebRTC provider and remain unavailable until configured.
      </p>
    </aside>
  );
}
