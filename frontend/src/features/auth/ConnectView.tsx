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
        <div className="brand-mark" aria-hidden="true">AI</div>
        <p className="eyebrow">Local-first workspace</p>
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
