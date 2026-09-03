import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  Connector,
  ConnectorAuthKind,
  ConnectorExecution,
  ConnectorExecutionRequest,
  ConnectorExecutionResult,
  ConnectorKind,
  ConnectorPlatform,
  ConnectorSettings,
  ConnectorWriteRequest,
  JsonValue,
} from "../../api/contracts";


interface ConnectorPanelProps {
  onClose: () => void;
  onLoadSettings: (signal?: AbortSignal) => Promise<ConnectorSettings>;
  onLoadPlatform: (signal?: AbortSignal) => Promise<ConnectorPlatform>;
  onLoad: (signal?: AbortSignal) => Promise<Connector[]>;
  onLoadAudit: (signal?: AbortSignal) => Promise<ConnectorExecution[]>;
  onCreate: (request: ConnectorWriteRequest) => Promise<Connector>;
  onHealth: (connectorId: string) => Promise<ConnectorExecutionResult>;
  onDiscover: (connectorId: string) => Promise<ConnectorExecutionResult>;
  onDisconnect: (connectorId: string) => Promise<Connector>;
  onReconnect: (connectorId: string) => Promise<ConnectorExecutionResult>;
  onExecute: (
    connectorId: string,
    request: ConnectorExecutionRequest,
  ) => Promise<ConnectorExecutionResult>;
  onRevoke: (connectorId: string) => Promise<Connector>;
}

const AUTH_KINDS: Array<{ value: ConnectorAuthKind; label: string }> = [
  { value: "none", label: "No credential" },
  { value: "bearer", label: "Bearer token" },
  { value: "api_key", label: "X-API-Key" },
  { value: "oauth2_bearer", label: "OAuth 2 bearer token" },
  { value: "oidc_bearer", label: "OIDC bearer token" },
];

function parseJson(value: string): JsonValue | undefined {
  if (value.trim() === "") return undefined;
  return JSON.parse(value) as JsonValue;
}

export function ConnectorPanel({
  onClose,
  onLoadSettings,
  onLoadPlatform,
  onLoad,
  onLoadAudit,
  onCreate,
  onHealth,
  onDiscover,
  onDisconnect,
  onReconnect,
  onExecute,
  onRevoke,
}: ConnectorPanelProps) {
  const [settings, setSettings] = useState<ConnectorSettings | null>(null);
  const [platform, setPlatform] = useState<ConnectorPlatform | null>(null);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [audit, setAudit] = useState<ConnectorExecution[]>([]);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("custom");
  const [service, setService] = useState("api");
  const [capabilities, setCapabilities] = useState("read");
  const [kind, setKind] = useState<ConnectorKind>("rest");
  const [origin, setOrigin] = useState("");
  const [authKind, setAuthKind] = useState<ConnectorAuthKind>("none");
  const [credential, setCredential] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [oauthTokenOrigin, setOauthTokenOrigin] = useState("");
  const [oauthTokenPath, setOauthTokenPath] = useState("");
  const [oauthExpiresAt, setOauthExpiresAt] = useState("");
  const [writeScope, setWriteScope] = useState(false);
  const [pathPrefix, setPathPrefix] = useState("/api/");
  const [healthPath, setHealthPath] = useState("/api/health");
  const [discoveryPath, setDiscoveryPath] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [method, setMethod] = useState<ConnectorExecutionRequest["method"]>("GET");
  const [path, setPath] = useState("/api/status");
  const [body, setBody] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastPayload, setLastPayload] = useState<JsonValue | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [nextSettings, nextPlatform, nextConnectors, nextAudit] = await Promise.all([
        onLoadSettings(signal),
        onLoadPlatform(signal),
        onLoad(signal),
        onLoadAudit(signal),
      ]);
      setSettings(nextSettings);
      setPlatform(nextPlatform);
      setConnectors(nextConnectors);
      setAudit(nextAudit);
      setOrigin((current) => current || nextSettings.allowed_origins[0] || "");
      setOauthTokenOrigin((current) => current || nextSettings.allowed_origins[0] || "");
      setSelectedId((current) =>
        nextConnectors.some((connector) => connector.id === current)
          ? current
          : nextConnectors.find((connector) => connector.revoked_at === null)?.id ?? "",
      );
      setNotice(null);
    } catch {
      if (!signal?.aborted) setNotice("Connector registry could not be loaded.");
    }
  }, [onLoad, onLoadAudit, onLoadPlatform, onLoadSettings]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const selected = useMemo(
    () => connectors.find((connector) => connector.id === selectedId) ?? null,
    [connectors, selectedId],
  );

  async function create() {
    const oauthAuth = authKind === "oauth2_bearer" || authKind === "oidc_bearer";
    const refreshValues = [
      refreshToken,
      oauthClientId,
      oauthClientSecret,
      oauthTokenPath,
      oauthExpiresAt,
    ];
    const refreshRequested = refreshValues.some((value) => value !== "");
    const refreshComplete = refreshValues.every((value) => value !== "") && oauthTokenOrigin !== "";
    const capabilityValues = capabilities.split(",").map((value) => value.trim()).filter(Boolean);
    if (
      busy || settings?.configured !== true || !name.trim() || !origin ||
      !pathPrefix || !healthPath ||
      !provider.trim() || !service.trim() || capabilityValues.length === 0 ||
      (authKind !== "none" && credential.length < 16) ||
      (refreshRequested && (!oauthAuth || !refreshComplete || refreshToken.length < 16 || oauthClientSecret.length < 16))
    ) return;
    setBusy(true);
    setNotice(null);
    try {
      const created = await onCreate({
        name: name.trim(),
        provider: provider.trim(),
        service: service.trim(),
        kind,
        base_url: origin,
        auth_kind: authKind,
        ...(authKind === "none" ? {} : refreshRequested ? {
          oauth2_credential: {
            access_token: credential,
            refresh_token: refreshToken,
            client_id: oauthClientId,
            client_secret: oauthClientSecret,
            token_origin: oauthTokenOrigin,
            token_path: oauthTokenPath,
            expires_at: new Date(oauthExpiresAt).toISOString(),
          },
        } : { credential }),
        scopes: writeScope ? ["read", "write"] : ["read"],
        capabilities: capabilityValues,
        path_prefixes: [pathPrefix],
        health_path: healthPath,
        ...(discoveryPath ? { discovery_path: discoveryPath } : {}),
        enabled: true,
        timeout_seconds: 5,
        max_retries: 1,
        rate_limit_requests_per_minute: 30,
      });
      setConnectors((current) => [created, ...current]);
      setSelectedId(created.id);
      setCredential("");
      setRefreshToken("");
      setOauthClientId("");
      setOauthClientSecret("");
      setOauthTokenOrigin(settings.allowed_origins[0] || "");
      setOauthTokenPath("");
      setOauthExpiresAt("");
      setName("");
      setNotice("Connector registered. Its credential is encrypted and write-only.");
    } catch {
      setNotice("Connector policy was rejected or could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function health(connectorId: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await onHealth(connectorId);
      await load();
      setNotice("Health check completed and was added to the audit history.");
    } catch {
      await load().catch(() => undefined);
      setNotice("Health check failed safely; inspect the audit failure state.");
    } finally {
      setBusy(false);
    }
  }

  async function discover(connectorId: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await onDiscover(connectorId);
      await load();
      setNotice("Provider capabilities were discovered, verified, and audited.");
    } catch {
      await load().catch(() => undefined);
      setNotice("Capability discovery failed safely; inspect its audit record.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect(connectorId: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await onDisconnect(connectorId);
      await load();
      setNotice("Connector disconnected. Its encrypted credential remains isolated for reconnection.");
    } catch {
      setNotice("Connector could not be disconnected.");
    } finally {
      setBusy(false);
    }
  }

  async function reconnect(connectorId: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await onReconnect(connectorId);
      await load();
      setNotice("Connector reconnected and passed a fresh audited health check.");
    } catch {
      await load().catch(() => undefined);
      setNotice("Reconnect health verification failed; no live status was claimed.");
    } finally {
      setBusy(false);
    }
  }

  async function execute() {
    if (busy || selected === null || !path) return;
    setBusy(true);
    setNotice(null);
    setLastPayload(null);
    try {
      const jsonBody = parseJson(body);
      const result = await onExecute(selected.id, {
        method,
        path,
        ...(jsonBody === undefined ? {} : { json_body: jsonBody }),
        ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
      });
      setLastPayload(result.payload);
      await load();
      setNotice("Connected action completed and was recorded in the audit history.");
    } catch {
      await load().catch(() => undefined);
      setNotice("Connected action failed safely; no success was reported.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(connectorId: string) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const revoked = await onRevoke(connectorId);
      setConnectors((current) => current.map((item) => item.id === revoked.id ? revoked : item));
      setNotice("Connector revoked and its encrypted credential removed.");
    } catch {
      setNotice("Connector could not be revoked.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="side-panel connector-panel" aria-labelledby="connector-heading">
      <header className="panel-header">
        <div>
          <p className="eyebrow">APPS / CONNECTIONS</p>
          <h2 id="connector-heading">Connected apps</h2>
        </div>
        <button type="button" className="button button-secondary" onClick={onClose}>Close</button>
      </header>
      <p className="field-help">
        Exact operator-approved origins only. Credentials are write-only, encrypted on the workstation,
        excluded from prompts and logs, and removed on revocation.
      </p>
      {platform !== null && <details>
        <summary>Connector platform coverage</summary>
        <p className="field-help">Lifecycle: {platform.lifecycle.join(" → ")}</p>
        {platform.capabilities.map((capability) => (
          <p className="field-help" key={capability.id}>
            {capability.label}: {capability.status.replaceAll("_", " ")}
            {capability.requirement === null ? "" : ` — ${capability.requirement}`}
          </p>
        ))}
      </details>}
      {notice !== null && <p role="status" className="notice">{notice}</p>}
      {settings?.configured !== true ? (
        <p className="notice notice-error">Configure the private connector state root on the backend first.</p>
      ) : settings.allowed_origins.length === 0 ? (
        <p className="notice">No egress origin is approved. Add an exact HTTPS or loopback origin in backend configuration.</p>
      ) : (
        <fieldset disabled={busy}>
          <legend>Register a connection</legend>
          <label>Name<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
          <label>Provider<input value={provider} maxLength={120} onChange={(event) => setProvider(event.target.value)} /></label>
          <label>Service<input value={service} maxLength={120} onChange={(event) => setService(event.target.value)} /></label>
          <label>Type<select value={kind} onChange={(event) => {
            const value = event.target.value as ConnectorKind;
            setKind(value);
            if (value === "webhook") setWriteScope(true);
          }}>
            <option value="rest">REST / JSON API</option>
            <option value="graphql">GraphQL / JSON API</option>
            <option value="webhook">Webhook</option>
            <option value="local_api">Local API</option>
          </select></label>
          <label>Approved origin<select value={origin} onChange={(event) => setOrigin(event.target.value)}>
            {settings.allowed_origins.map((item) => <option key={item}>{item}</option>)}
          </select></label>
          <label>Authentication<select value={authKind} onChange={(event) => setAuthKind(event.target.value as ConnectorAuthKind)}>
            {AUTH_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select></label>
          {authKind !== "none" && <label>Credential<input type="password" autoComplete="off" value={credential} minLength={16} maxLength={2048} onChange={(event) => setCredential(event.target.value)} /></label>}
          {(authKind === "oauth2_bearer" || authKind === "oidc_bearer") && <details>
            <summary>Automatic token refresh (optional)</summary>
            <p className="field-help">Complete every field to enable refresh. All token and client-secret values remain encrypted and write-only.</p>
            <label>Refresh token<input type="password" autoComplete="off" value={refreshToken} minLength={16} maxLength={2048} onChange={(event) => setRefreshToken(event.target.value)} /></label>
            <label>Client ID<input autoComplete="off" value={oauthClientId} maxLength={256} onChange={(event) => setOauthClientId(event.target.value)} /></label>
            <label>Client secret<input type="password" autoComplete="off" value={oauthClientSecret} minLength={16} maxLength={2048} onChange={(event) => setOauthClientSecret(event.target.value)} /></label>
            <label>Token origin<select value={oauthTokenOrigin} onChange={(event) => setOauthTokenOrigin(event.target.value)}>
              {settings.allowed_origins.map((item) => <option key={item}>{item}</option>)}
            </select></label>
            <label>Token path<input value={oauthTokenPath} maxLength={512} placeholder="/oauth/token" onChange={(event) => setOauthTokenPath(event.target.value)} /></label>
            <label>Access-token expiry<input type="datetime-local" value={oauthExpiresAt} onChange={(event) => setOauthExpiresAt(event.target.value)} /></label>
          </details>}
          <label>Capabilities (comma separated)<input value={capabilities} maxLength={2048} onChange={(event) => setCapabilities(event.target.value)} /></label>
          <label>Allowed path prefix<input value={pathPrefix} maxLength={512} onChange={(event) => setPathPrefix(event.target.value)} /></label>
          <label>Health path<input value={healthPath} maxLength={512} onChange={(event) => setHealthPath(event.target.value)} /></label>
          <label>Discovery path (optional)<input value={discoveryPath} maxLength={512} onChange={(event) => setDiscoveryPath(event.target.value)} /></label>
          <label className="checkbox-row"><input type="checkbox" checked={writeScope} onChange={(event) => setWriteScope(event.target.checked)} />Permit write actions</label>
          <button type="button" className="button button-primary" onClick={() => void create()}>Register connection</button>
        </fieldset>
      )}

      <div className="connector-list">
        {connectors.map((connector) => (
          <article className="connector-card" key={connector.id}>
            <div className="panel-header">
              <strong>{connector.name}</strong>
              <span className="status-pill">{connector.connection_status.replaceAll("_", " ")}</span>
            </div>
            <p className="field-help">{connector.provider} · {connector.service} · {connector.kind.replaceAll("_", " ")} · {connector.base_url}</p>
            <p className="field-help">Capabilities: {connector.capabilities.join(", ")}</p>
            <p className="field-help">Scopes: {connector.scopes.join(", ")} · Paths: {connector.path_prefixes.join(", ")}</p>
            <p className="field-help">Health: {connector.connection_status.replaceAll("_", " ")} · Last health check: {connector.last_health_checked_at ?? "not yet checked"}</p>
            <p className="field-help">Rate limit: {connector.rate_limit_requests_per_minute}/min · Timeout: {connector.timeout_seconds}s · Retries: {connector.max_retries}</p>
            <p className="field-help">Credential: {connector.credential_configured ? "configured (write-only)" : "none"}</p>
            <p className="field-help">Last successful test: {connector.last_successful_test_at ?? "not yet verified"} · Audit: {connector.audit_reference ?? "none"}</p>
            {connector.revoked_at === null && <div className="button-row">
              {connector.enabled ? <>
                <button type="button" className="button button-secondary" disabled={busy} onClick={() => void health(connector.id)}>Health check</button>
                {connector.discovery_path !== null && <button type="button" className="button button-secondary" disabled={busy} onClick={() => void discover(connector.id)}>Discover</button>}
                <button type="button" className="button button-secondary" disabled={busy} onClick={() => void disconnect(connector.id)}>Disconnect</button>
              </> : <button type="button" className="button button-secondary" disabled={busy} onClick={() => void reconnect(connector.id)}>Reconnect & verify</button>}
              <button type="button" className="button button-secondary" disabled={busy} onClick={() => void revoke(connector.id)}>Revoke</button>
            </div>}
          </article>
        ))}
      </div>

      {connectors.some((connector) => connector.revoked_at === null) && <fieldset disabled={busy}>
        <legend>Execute an approved JSON action</legend>
        <label>Connection<select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
          {connectors.filter((connector) => connector.revoked_at === null).map((connector) => <option key={connector.id} value={connector.id}>{connector.name}</option>)}
        </select></label>
        <label>Method<select value={method} onChange={(event) => setMethod(event.target.value as ConnectorExecutionRequest["method"])}>
          {(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] as const).map((item) => <option key={item}>{item}</option>)}
        </select></label>
        <label>Path<input value={path} maxLength={512} onChange={(event) => setPath(event.target.value)} /></label>
        <label>JSON body<textarea value={body} placeholder='{"action":"sync"}' onChange={(event) => setBody(event.target.value)} /></label>
        <label>Idempotency key<input value={idempotencyKey} minLength={16} maxLength={128} onChange={(event) => setIdempotencyKey(event.target.value)} /></label>
        <button type="button" className="button button-primary" onClick={() => void execute()}>Execute connected action</button>
        {lastPayload !== null && <pre className="connector-result">{JSON.stringify(lastPayload, null, 2)}</pre>}
      </fieldset>}

      <section aria-labelledby="connector-audit-heading">
        <h3 id="connector-audit-heading">Audit history</h3>
        {audit.length === 0 ? <p className="field-help">No connector actions recorded.</p> : audit.map((item) => (
          <p className="field-help" key={item.id}>
            {item.action} · {item.method} {item.path} · {item.status} · {item.attempts} attempt(s) · {item.duration_ms} ms
          </p>
        ))}
      </section>
    </section>
  );
}
