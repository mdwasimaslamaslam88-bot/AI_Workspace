import { type FormEvent, useRef } from "react";

interface ConnectViewProps {
  connecting: boolean;
  error: string | null;
  onConnect: (token: string) => Promise<void>;
}

export function ConnectView({
  connecting,
  error,
  onConnect,
}: ConnectViewProps) {
  const tokenInput = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = tokenInput.current;
    const token = input?.value.trim() ?? "";
    if (input !== null) input.value = "";
    if (token.length === 0) return;
    await onConnect(token);
  }

  return (
    <main className="connect-shell">
      <section className="connect-card" aria-labelledby="connect-title">
        <img className="brand-icon" src="/icons/icon-192.png" alt="" />
        <p className="eyebrow">WORK STATION</p>
        <h1 id="connect-title">Connect to your Personal AI</h1>
        <p className="muted">
          Enter an already-provisioned bearer token. It stays in this browser
          session and is sent only to your configured backend.
        </p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="bearer-token">Bearer token</label>
          <input
            ref={tokenInput}
            id="bearer-token"
            name="bearer-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            required
            disabled={connecting}
            aria-describedby="token-help"
          />
          <p id="token-help" className="field-help">
            User provisioning remains an operator action.
          </p>
          {error !== null && (
            <p className="notice notice-error" role="alert">{error}</p>
          )}
          <button className="button button-primary full-width" disabled={connecting}>
            {connecting ? "Connecting…" : "Connect"}
          </button>
        </form>
      </section>
    </main>
  );
}

interface ReconnectViewProps {
  online: boolean;
  reconnecting: boolean;
  error: string;
  onRetry: () => void;
  onUseDifferentToken: () => void;
}

export function ReconnectView({
  online,
  reconnecting,
  error,
  onRetry,
  onUseDifferentToken,
}: ReconnectViewProps) {
  return (
    <main className="connect-shell">
      <section className="connect-card" aria-labelledby="reconnect-title">
        <img className="brand-icon" src="/icons/icon-192.png" alt="" />
        <p className="eyebrow">WORK STATION</p>
        <h1 id="reconnect-title">
          {online ? "Backend unavailable" : "You are offline"}
        </h1>
        <p className="muted">{error}</p>
        <p className="connection-preserved" role="status">
          Your saved session is preserved. No credential needs to be entered again.
        </p>
        <div className="reconnect-actions">
          <button
            type="button"
            className="button button-primary"
            onClick={onRetry}
            disabled={reconnecting || !online}
          >
            {reconnecting ? "Reconnecting…" : "Retry connection"}
          </button>
          <button
            type="button"
            className="button button-quiet"
            onClick={onUseDifferentToken}
            disabled={reconnecting}
          >
            Use a different token
          </button>
        </div>
      </section>
    </main>
  );
}
