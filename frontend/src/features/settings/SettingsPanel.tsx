import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ProductCapability,
  ProductCapabilityId,
  ProductCapabilityReason,
  SystemDiagnostics,
  UserSession,
  UserSessionProvision,
} from "../../api/contracts";
import type { AppearancePreference } from "../../preferences/appearance";

interface SettingsPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<ProductCapability[]>;
  onLoadDiagnostics: (signal?: AbortSignal) => Promise<SystemDiagnostics>;
  onLoadSessions: (signal?: AbortSignal) => Promise<UserSession[]>;
  appearance: AppearancePreference;
  onAppearanceChange: (value: AppearancePreference) => void;
  onRotateSession: (signal?: AbortSignal) => Promise<void>;
  onCreateSession: (label: string | null) => Promise<UserSessionProvision>;
  onRenameCurrentSession: (label: string | null) => Promise<UserSession>;
  onRevokeSession: (sessionId: string) => Promise<void>;
  onLogout: () => Promise<void>;
  onManageMemory: () => void;
}

const CAPABILITY_LABELS: Record<ProductCapabilityId, string> = {
  chat: "Text chat",
  vision_input: "Vision input",
  attachments: "Owned attachments",
  documents_rag: "Document intelligence & RAG",
  personal_memory: "Personal memory",
  bounded_tools: "Bounded local tools",
  bounded_workflows: "Bounded workflows",
  image_generation: "Image generation",
  image_editing: "Image editing",
  voice_input: "Voice input",
  voice_output: "Voice output",
};

const BLOCKER_COPY: Record<ProductCapabilityReason, string> = {
  asset_storage_required:
    "Configure a private absolute asset storage root on the backend.",
  local_model_runtime_unavailable:
    "Make the configured loopback model runtime available.",
  allowlisted_text_model_required:
    "Allowlist an available local model with text generation.",
  allowlisted_vision_model_required:
    "Allowlist an available local model with text generation and vision input.",
  local_image_runtime_and_model_required:
    "Install and implement a bounded loopback image adapter with an allowlisted local text-to-image model.",
  local_image_edit_runtime_and_model_required:
    "Install and implement a bounded loopback image-edit adapter with an allowlisted local image-to-image or inpainting model.",
  local_voice_runtime_and_models_required:
    "Install bounded audio decoding plus implemented local speech-to-text and text-to-speech adapters and models.",
};

const SERVICE_LABELS: Record<SystemDiagnostics["services"][number]["id"], string> = {
  backend: "Backend",
  database: "Database",
  redis: "Redis",
  ollama: "Ollama",
  vision: "Vision",
  image_runtime: "Image runtime",
  speech_to_text: "Speech to text",
  text_to_speech: "Text to speech",
  storage: "Private storage",
  remote_gateway: "Remote gateway",
  gpu: "GPU",
};

export function SettingsPanel({
  onClose,
  onLoad,
  onLoadDiagnostics,
  onLoadSessions,
  appearance,
  onAppearanceChange,
  onRotateSession,
  onCreateSession,
  onRenameCurrentSession,
  onRevokeSession,
  onLogout,
  onManageMemory,
}: SettingsPanelProps) {
  const [capabilities, setCapabilities] = useState<ProductCapability[]>([]);
  const [diagnostics, setDiagnostics] = useState<SystemDiagnostics | null>(null);
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionNotice, setSessionNotice] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);
  const [currentSessionLabel, setCurrentSessionLabel] = useState("");
  const [newSessionLabel, setNewSessionLabel] = useState("");
  const [issuedSession, setIssuedSession] =
    useState<UserSessionProvision | null>(null);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError(null);
    try {
      const [items, diagnosticSnapshot, accessSessions] = await Promise.all([
        onLoad(current.signal),
        onLoadDiagnostics(current.signal),
        onLoadSessions(current.signal),
      ]);
      if (!current.signal.aborted) {
        setCapabilities(items);
        setDiagnostics(diagnosticSnapshot);
        setSessions(accessSessions);
        setCurrentSessionLabel(
          accessSessions.find((item) => item.is_current)?.label ?? "",
        );
      }
    } catch {
      if (!current.signal.aborted) {
        setError("Capability diagnostics could not be loaded.");
      }
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, [onLoad, onLoadDiagnostics, onLoadSessions]);

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
  }, [load]);

  const available = capabilities.filter(
    (capability) => capability.status === "available",
  ).length;

  const rotateSession = useCallback(async () => {
    setSessionBusy(true);
    setSessionNotice(null);
    try {
      await onRotateSession();
      setSessionNotice({
        kind: "success",
        message: "Owner access token rotated and saved on this device.",
      });
    } catch {
      setSessionNotice({
        kind: "error",
        message:
          "The session could not be rotated safely. Keep this app open and retry.",
      });
    } finally {
      setSessionBusy(false);
    }
  }, [onRotateSession]);

  const renameCurrentSession = useCallback(async () => {
    setSessionBusy(true);
    setSessionNotice(null);
    try {
      const renamed = await onRenameCurrentSession(
        currentSessionLabel.trim() || null,
      );
      setSessions((items) =>
        items.map((item) => (item.id === renamed.id ? renamed : item)),
      );
      setSessionNotice({ kind: "success", message: "Device label updated." });
    } catch {
      setSessionNotice({ kind: "error", message: "The device label could not be updated." });
    } finally {
      setSessionBusy(false);
    }
  }, [currentSessionLabel, onRenameCurrentSession]);

  const createSession = useCallback(async () => {
    setSessionBusy(true);
    setSessionNotice(null);
    setIssuedSession(null);
    try {
      const created = await onCreateSession(newSessionLabel.trim() || null);
      setSessions((items) => [created.session, ...items]);
      setIssuedSession(created);
      setNewSessionLabel("");
    } catch {
      setSessionNotice({
        kind: "error",
        message: "A new device session could not be created.",
      });
    } finally {
      setSessionBusy(false);
    }
  }, [newSessionLabel, onCreateSession]);

  const revokeSession = useCallback(
    async (sessionId: string) => {
      setSessionBusy(true);
      setSessionNotice(null);
      try {
        await onRevokeSession(sessionId);
        setSessions((items) => items.filter((item) => item.id !== sessionId));
        setSessionNotice({ kind: "success", message: "Device session revoked." });
      } catch {
        setSessionNotice({ kind: "error", message: "The device session could not be revoked." });
      } finally {
        setSessionBusy(false);
      }
    },
    [onRevokeSession],
  );

  const copyIssuedToken = useCallback(async () => {
    if (issuedSession === null) return;
    try {
      if (navigator.clipboard?.writeText === undefined) {
        throw new Error("Clipboard unavailable");
      }
      await navigator.clipboard.writeText(issuedSession.access_token);
      setSessionNotice({ kind: "success", message: "Token copied." });
    } catch {
      setSessionNotice({
        kind: "error",
        message: "Copy failed. Select the token manually.",
      });
    }
  }, [issuedSession]);

  return (
    <aside className="settings-panel" aria-labelledby="settings-panel-title">
      <header className="settings-panel-header">
        <div>
          <p className="eyebrow">Owner product state</p>
          <h2 id="settings-panel-title">Settings & diagnostics</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Close
        </button>
      </header>

      <section aria-labelledby="account-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="account-title">Account & sessions</h3>
            <p className="field-help">
              Each device has a separately revocable owner credential. Tokens
              are shown only when issued and are never included in this list.
            </p>
          </div>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            className="button button-secondary"
            disabled={sessionBusy}
            onClick={() => void rotateSession()}
          >
            {sessionBusy ? "Rotating…" : "Rotate owner token"}
          </button>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => void onLogout()}
          >
            Log out on this device
          </button>
        </div>
        <div className="session-controls">
          <label>
            <span>This device label</span>
            <input
              value={currentSessionLabel}
              maxLength={80}
              placeholder="This browser"
              onChange={(event) => setCurrentSessionLabel(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="button button-secondary"
            disabled={sessionBusy}
            onClick={() => void renameCurrentSession()}
          >
            Save label
          </button>
        </div>
        <div className="session-controls">
          <label>
            <span>New device label</span>
            <input
              value={newSessionLabel}
              maxLength={80}
              placeholder="Phone or laptop"
              onChange={(event) => setNewSessionLabel(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="button button-secondary"
            disabled={sessionBusy}
            onClick={() => void createSession()}
          >
            Issue device token
          </button>
        </div>
        {issuedSession !== null && (
          <div className="issued-session" role="status">
            <strong>Copy this token now. It will not be shown again.</strong>
            <input
              aria-label="New device access token"
              type="password"
              readOnly
              value={issuedSession.access_token}
            />
            <div className="settings-actions">
              <button
                type="button"
                className="button button-secondary"
                onClick={() => void copyIssuedToken()}
              >
                Copy token
              </button>
              <button
                type="button"
                className="button button-quiet"
                onClick={() => setIssuedSession(null)}
              >
                I saved it
              </button>
            </div>
          </div>
        )}
        <ul className="session-list" aria-label="Active device sessions">
          {sessions.map((accessSession) => (
            <li key={accessSession.id}>
              <div>
                <strong>{accessSession.label ?? "Unnamed device"}</strong>
                <span>
                  {accessSession.is_current ? "Current device" : "Active device"}
                  {" · "}
                  {new Date(accessSession.updated_at).toLocaleDateString()}
                </span>
              </div>
              {!accessSession.is_current && (
                <button
                  type="button"
                className="button button-quiet"
                disabled={sessionBusy}
                aria-label={`Revoke ${accessSession.label ?? "unnamed device"}`}
                onClick={() => void revokeSession(accessSession.id)}
                >
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
        {sessionNotice !== null && (
          <p
            className={`notice ${sessionNotice.kind === "error" ? "notice-error" : "notice-success"}`}
            role={sessionNotice.kind === "error" ? "alert" : "status"}
          >
            {sessionNotice.message}
          </p>
        )}
      </section>

      <section aria-labelledby="appearance-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="appearance-title">Appearance</h3>
            <p className="field-help">Use the device theme or a fixed accessible palette.</p>
          </div>
          <label className="appearance-control">
            <span>Theme</span>
            <select
              value={appearance}
              onChange={(event) =>
                onAppearanceChange(event.target.value as AppearancePreference)
              }
            >
              <option value="system">System</option>
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </label>
        </div>
      </section>

      <section aria-labelledby="personalization-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="personalization-title">Personalization</h3>
            <p className="field-help">
              Memory is explicit, inspectable, optional, and forgettable.
            </p>
          </div>
          <button
            type="button"
            className="button button-secondary"
            onClick={onManageMemory}
          >
            Manage memory
          </button>
        </div>
      </section>

      <section aria-labelledby="model-settings-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="model-settings-title">Model, voice & storage</h3>
            <p className="field-help">
              Model choice remains in the workspace toolbar. Runtime and
              storage readiness below comes from the authenticated backend.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="capabilities-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="capabilities-title">Product capabilities</h3>
            {!loading && error === null && (
              <p className="field-help" role="status">
                {available} of {capabilities.length} capabilities available now.
              </p>
            )}
          </div>
          <button
            type="button"
            className="button button-quiet"
            onClick={() => void load()}
            disabled={loading}
          >
            Refresh
          </button>
        </div>

        {loading && <p role="status">Checking local capabilities…</p>}
        {error !== null && (
          <p className="notice notice-error" role="alert">{error}</p>
        )}
        {!loading && error === null && (
          <ul className="capability-list" aria-label="Product capability status">
            {capabilities.map((capability) => (
              <li key={capability.id}>
                <div className="capability-heading">
                  <strong>{CAPABILITY_LABELS[capability.id]}</strong>
                  <span
                    className={`capability-status capability-status-${capability.status}`}
                  >
                    {capability.status}
                  </span>
                </div>
                {capability.blocking_reasons.length > 0 && (
                  <ul className="capability-blockers">
                    {capability.blocking_reasons.map((reason) => (
                      <li key={reason}>{BLOCKER_COPY[reason]}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="notification-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="notification-title">Notifications & security</h3>
            <p className="field-help">
              Completion alerts are generic by default. Prompts, responses,
              conversation names, filenames, tokens, and local paths are omitted.
            </p>
          </div>
        </div>
      </section>

      <section aria-labelledby="diagnostics-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="diagnostics-title">Private system diagnostics</h3>
            {diagnostics !== null && (
              <p className="field-help">
                Connection mode: {diagnostics.mode.toUpperCase()}
              </p>
            )}
          </div>
        </div>
        {diagnostics !== null && (
          <>
            <ul className="capability-list" aria-label="Private service status">
              {diagnostics.services.map((service) => (
                <li key={service.id}>
                  <div className="capability-heading">
                    <strong>{SERVICE_LABELS[service.id]}</strong>
                    <span
                      className={`capability-status capability-status-${service.status}`}
                    >
                      {service.status}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
            {diagnostics.gpus.map((gpu) => (
              <p className="field-help" key={`${gpu.model}-${gpu.vram_bytes}`}>
                {gpu.model} · {Math.round(gpu.vram_bytes / 1024 ** 3)} GiB · {gpu.status}
              </p>
            ))}
          </>
        )}
      </section>

      <p className="settings-safety">
        Diagnostics expose only fixed capability states. Tokens, local paths,
        runtime URLs, model responses, and private content are never displayed.
      </p>
    </aside>
  );
}
