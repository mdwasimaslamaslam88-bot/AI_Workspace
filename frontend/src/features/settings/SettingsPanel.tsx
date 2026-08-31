import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ExternalAISettings,
  ExternalProvider,
  ExternalProviderKind,
  ExternalProviderUpsertRequest,
  ModelTask,
  ProductCapability,
  ProductCapabilityId,
  ProductCapabilityReason,
  SelfUpdateStatus,
  SystemDiagnostics,
  UserSession,
  UserSessionProvision,
} from "../../api/contracts";
import type { AppearancePreference } from "../../preferences/appearance";
import {
  isDesktopRuntime,
  readDesktopAutostartEnabled,
  readDesktopNotificationPermission,
  requestDesktopNotificationPermission,
  setDesktopContentProtected,
  writeDesktopAutostartEnabled,
} from "../../platform/desktop";

interface SettingsPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<ProductCapability[]>;
  onLoadDiagnostics: (signal?: AbortSignal) => Promise<SystemDiagnostics>;
  onLoadExternalAI: (signal?: AbortSignal) => Promise<ExternalAISettings>;
  onSetExternalAIEnabled: (enabled: boolean) => Promise<ExternalAISettings>;
  onUpsertExternalAIProvider: (
    providerId: string,
    provider: ExternalProviderUpsertRequest,
  ) => Promise<ExternalAISettings>;
  onSetExternalAIProviderEnabled: (
    provider: ExternalProvider,
    enabled: boolean,
  ) => Promise<ExternalAISettings>;
  onDeleteExternalAIProvider: (providerId: string) => Promise<ExternalAISettings>;
  onLoadSelfUpdate: (signal?: AbortSignal) => Promise<SelfUpdateStatus>;
  onDecideSelfUpdate: (decision: "update" | "cancel") => Promise<SelfUpdateStatus>;
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
  onLoadExternalAI,
  onSetExternalAIEnabled,
  onUpsertExternalAIProvider,
  onSetExternalAIProviderEnabled,
  onDeleteExternalAIProvider,
  onLoadSelfUpdate,
  onDecideSelfUpdate,
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
  const [externalAI, setExternalAI] = useState<ExternalAISettings | null>(null);
  const [selfUpdate, setSelfUpdate] = useState<SelfUpdateStatus | null>(null);
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionNotice, setSessionNotice] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);
  const [externalBusy, setExternalBusy] = useState(false);
  const [externalNotice, setExternalNotice] = useState<string | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [updateNotice, setUpdateNotice] = useState<string | null>(null);
  const [providerId, setProviderId] = useState("");
  const [providerKind, setProviderKind] = useState<ExternalProviderKind>("openai");
  const [providerKey, setProviderKey] = useState("");
  const [providerModel, setProviderModel] = useState("");
  const [providerTask, setProviderTask] = useState<ModelTask>("general_chat");
  const [providerEvidence, setProviderEvidence] = useState("");
  const [currentSessionLabel, setCurrentSessionLabel] = useState("");
  const [newSessionLabel, setNewSessionLabel] = useState("");
  const [issuedSession, setIssuedSession] =
    useState<UserSessionProvision | null>(null);
  const desktopRuntime = isDesktopRuntime();
  const [desktopAutostart, setDesktopAutostart] = useState(false);
  const [desktopNotifications, setDesktopNotifications] = useState(false);
  const [desktopBusy, setDesktopBusy] = useState(false);
  const [desktopNotice, setDesktopNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError(null);
    try {
      const [items, diagnosticSnapshot, externalSnapshot, updateSnapshot, accessSessions] = await Promise.all([
        onLoad(current.signal),
        onLoadDiagnostics(current.signal),
        onLoadExternalAI(current.signal),
        onLoadSelfUpdate(current.signal),
        onLoadSessions(current.signal),
      ]);
      if (!current.signal.aborted) {
        setCapabilities(items);
        setDiagnostics(diagnosticSnapshot);
        setExternalAI(externalSnapshot);
        setSelfUpdate(updateSnapshot);
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
  }, [onLoad, onLoadDiagnostics, onLoadExternalAI, onLoadSelfUpdate, onLoadSessions]);

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
  }, [load]);

  useEffect(() => {
    if (!desktopRuntime) return;
    let active = true;
    void Promise.all([
      readDesktopAutostartEnabled(),
      readDesktopNotificationPermission(),
    ])
      .then(([autostartEnabled, notificationsEnabled]) => {
        if (!active) return;
        setDesktopAutostart(autostartEnabled);
        setDesktopNotifications(notificationsEnabled);
      })
      .catch(() => {
        if (active) setDesktopNotice("Desktop preferences could not be read.");
      });
    return () => {
      active = false;
    };
  }, [desktopRuntime]);

  useEffect(() => {
    const clearTransientCredential = () => {
      if (document.visibilityState !== "visible") {
        setIssuedSession(null);
        setProviderKey("");
      }
    };
    document.addEventListener("visibilitychange", clearTransientCredential);
    window.addEventListener("pagehide", clearTransientCredential);
    return () => {
      document.removeEventListener("visibilitychange", clearTransientCredential);
      window.removeEventListener("pagehide", clearTransientCredential);
    };
  }, []);

  useEffect(() => {
    if (!desktopRuntime) return;
    let active = true;
    const protectedContent = issuedSession !== null;
    void setDesktopContentProtected(protectedContent).catch(() => {
      if (active && protectedContent) {
        setIssuedSession(null);
        setSessionNotice({
          kind: "error",
          message: "The one-time token view could not be protected. Issue a new device token after desktop protection is available.",
        });
      }
    });
    return () => {
      active = false;
      if (protectedContent) {
        void setDesktopContentProtected(false).catch(() => undefined);
      }
    };
  }, [desktopRuntime, issuedSession]);

  const available = capabilities.filter(
    (capability) => capability.status === "available",
  ).length;

  const setExternalEnabled = useCallback(async (enabled: boolean) => {
    setExternalBusy(true);
    setExternalNotice(null);
    try {
      setExternalAI(await onSetExternalAIEnabled(enabled));
      setExternalNotice(enabled ? "External fallback enabled." : "External fallback disabled.");
    } catch {
      setExternalNotice("External fallback could not be updated.");
    } finally {
      setExternalBusy(false);
    }
  }, [onSetExternalAIEnabled]);

  const addExternalProvider = useCallback(async () => {
    const normalizedId = providerId.trim();
    const normalizedModel = providerModel.trim();
    const normalizedEvidence = providerEvidence.trim();
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(normalizedId) || providerKey.length < 16) {
      setExternalNotice("Provider ID or key is invalid.");
      return;
    }
    if (normalizedEvidence !== "" && !/^[a-f0-9]{64}$/.test(normalizedEvidence)) {
      setExternalNotice("Verification evidence must be a lowercase SHA-256 digest.");
      return;
    }
    setExternalBusy(true);
    setExternalNotice(null);
    try {
      const models = normalizedModel === "" ? [] : [{
        model_id: normalizedModel,
        tasks: [providerTask],
        verified: normalizedEvidence !== "",
        ...(normalizedEvidence === "" ? {} : { verification_evidence_sha256: normalizedEvidence }),
        measured_quality: 0,
        measured_latency_ms: 0,
        stability_rate: 0,
        context_window: 0,
        input_cost_micros_per_million_tokens: 0,
        output_cost_micros_per_million_tokens: 0,
      }];
      setExternalAI(await onUpsertExternalAIProvider(normalizedId, {
        kind: providerKind,
        api_key: providerKey,
        enabled: false,
        models,
      }));
      setProviderKey("");
      setProviderModel("");
      setProviderEvidence("");
      setExternalNotice("Provider saved with fallback disabled.");
    } catch {
      setExternalNotice("Provider could not be saved.");
    } finally {
      setExternalBusy(false);
    }
  }, [onUpsertExternalAIProvider, providerEvidence, providerId, providerKey, providerKind, providerModel, providerTask]);

  const setProviderEnabled = useCallback(async (
    provider: ExternalProvider,
    enabled: boolean,
  ) => {
    setExternalBusy(true);
    setExternalNotice(null);
    try {
      setExternalAI(await onSetExternalAIProviderEnabled(provider, enabled));
      setExternalNotice(`${provider.provider_id} ${enabled ? "enabled" : "disabled"}.`);
    } catch {
      setExternalNotice("Provider state could not be updated.");
    } finally {
      setExternalBusy(false);
    }
  }, [onSetExternalAIProviderEnabled]);

  const deleteProvider = useCallback(async (id: string) => {
    setExternalBusy(true);
    setExternalNotice(null);
    try {
      setExternalAI(await onDeleteExternalAIProvider(id));
      setExternalNotice("Provider and encrypted key deleted.");
    } catch {
      setExternalNotice("Provider could not be deleted.");
    } finally {
      setExternalBusy(false);
    }
  }, [onDeleteExternalAIProvider]);

  const decideUpdate = useCallback(async (decision: "update" | "cancel") => {
    setUpdateBusy(true);
    setUpdateNotice(null);
    try {
      const state = await onDecideSelfUpdate(decision);
      setSelfUpdate(state);
      setUpdateNotice(
        decision === "update"
          ? "Validated release activated. Health monitoring and automatic rollback remain armed."
          : "Validated update cancelled; production was unchanged.",
      );
    } catch {
      setUpdateNotice("The update decision could not be applied safely.");
    } finally {
      setUpdateBusy(false);
    }
  }, [onDecideSelfUpdate]);

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

  const updateDesktopAutostart = useCallback(async (enabled: boolean) => {
    setDesktopBusy(true);
    setDesktopNotice(null);
    try {
      await writeDesktopAutostartEnabled(enabled);
      setDesktopAutostart(enabled);
      setDesktopNotice(enabled ? "Desktop startup enabled." : "Desktop startup disabled.");
    } catch {
      setDesktopNotice("Desktop startup preference could not be changed.");
    } finally {
      setDesktopBusy(false);
    }
  }, []);

  const enableDesktopNotifications = useCallback(async () => {
    setDesktopBusy(true);
    setDesktopNotice(null);
    try {
      const granted = await requestDesktopNotificationPermission();
      setDesktopNotifications(granted);
      setDesktopNotice(
        granted
          ? "Private desktop notifications enabled."
          : "Desktop notification permission was not granted.",
      );
    } catch {
      setDesktopNotice("Desktop notification preference could not be changed.");
    } finally {
      setDesktopBusy(false);
    }
  }, []);

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
            <span className="field-help">
              This one-time view clears when the app leaves the foreground. The
              packaged desktop window also blocks capture while it is visible.
            </span>
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

      <section aria-labelledby="external-ai-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="external-ai-title">API / External AI</h3>
            <p className="field-help">
              Local models are always tried first. Only verified provider models
              can enter fallback routing; keys are write-only and encrypted by
              the workstation backend.
            </p>
          </div>
          <label className="desktop-preference-toggle">
            <input
              type="checkbox"
              checked={externalAI?.global_enabled ?? false}
              disabled={externalBusy || externalAI?.configured !== true}
              onChange={(event) => void setExternalEnabled(event.target.checked)}
            />
            <span>API fallback</span>
          </label>
        </div>
        {externalAI?.configured === false && (
          <p className="notice" role="status">
            Configure the owner-only External AI state root on the backend to
            enable provider management.
          </p>
        )}
        {externalAI?.configured === true && (
          <>
            <ul className="capability-list" aria-label="External AI providers">
              {externalAI.providers.map((provider) => (
                <li key={provider.provider_id}>
                  <div className="capability-heading">
                    <div>
                      <strong>{provider.provider_id}</strong>
                      <span className="field-help">
                        {provider.kind} · {provider.status} · key stored
                      </span>
                    </div>
                    <label className="desktop-preference-toggle">
                      <input
                        type="checkbox"
                        aria-label={`Enable ${provider.provider_id}`}
                        checked={provider.enabled}
                        disabled={externalBusy}
                        onChange={(event) => void setProviderEnabled(provider, event.target.checked)}
                      />
                      <span>Enabled</span>
                    </label>
                  </div>
                  <p className="field-help">
                    Spend: ${(provider.spent_micros / 1_000_000).toFixed(2)}
                    {provider.spending_limit_micros > 0
                      ? ` / $${(provider.spending_limit_micros / 1_000_000).toFixed(2)}`
                      : " · no configured spend ceiling"}
                    {provider.quota_remaining_tokens === null
                      ? ""
                      : ` · ${provider.quota_remaining_tokens} quota tokens left`}
                  </p>
                  {provider.models.map((model) => (
                    <p className="field-help" key={model.model_id}>
                      {model.model_id} · {model.verified ? "verified" : "verification required"}
                    </p>
                  ))}
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={externalBusy}
                    onClick={() => void deleteProvider(provider.provider_id)}
                  >
                    Delete provider
                  </button>
                </li>
              ))}
            </ul>
            <div className="session-controls">
              <label>
                <span>Provider ID</span>
                <input
                  value={providerId}
                  maxLength={64}
                  placeholder="openai-primary"
                  autoComplete="off"
                  onChange={(event) => setProviderId(event.target.value)}
                />
              </label>
              <label>
                <span>Provider</span>
                <select
                  value={providerKind}
                  onChange={(event) => setProviderKind(event.target.value as ExternalProviderKind)}
                >
                  {externalAI.supported_provider_kinds.map((kind) => (
                    <option value={kind} key={kind}>{kind}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="session-controls">
              <label>
                <span>API key (write-only)</span>
                <input
                  type="password"
                  value={providerKey}
                  minLength={16}
                  maxLength={512}
                  autoComplete="new-password"
                  spellCheck={false}
                  onChange={(event) => setProviderKey(event.target.value)}
                />
              </label>
              <label>
                <span>Model ID (optional)</span>
                <input
                  value={providerModel}
                  maxLength={128}
                  autoComplete="off"
                  onChange={(event) => setProviderModel(event.target.value)}
                />
              </label>
            </div>
            <div className="session-controls">
              <label>
                <span>Model task</span>
                <select
                  value={providerTask}
                  onChange={(event) => setProviderTask(event.target.value as ModelTask)}
                >
                  <option value="general_chat">General</option>
                  <option value="reasoning">Reasoning</option>
                  <option value="mathematics">Mathematics</option>
                  <option value="coding">Coding</option>
                  <option value="code_generation">Code generation</option>
                  <option value="debugging">Debugging</option>
                  <option value="expert_analysis">Expert analysis</option>
                  <option value="long_context">Long context</option>
                  <option value="exact_output">Exact output</option>
                </select>
              </label>
              <label>
                <span>Benchmark evidence SHA-256 (optional)</span>
                <input
                  value={providerEvidence}
                  maxLength={64}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(event) => setProviderEvidence(event.target.value)}
                />
              </label>
            </div>
            <button
              type="button"
              className="button button-secondary"
              disabled={externalBusy}
              onClick={() => void addExternalProvider()}
            >
              Save provider disabled
            </button>
          </>
        )}
        {externalNotice !== null && <p role="status">{externalNotice}</p>}
      </section>

      <section aria-labelledby="self-update-title" className="settings-section">
        <div className="settings-section-heading">
          <div>
            <h3 id="self-update-title">Secure self-update</h3>
            <p className="field-help">
              Candidate releases stay isolated from production until every
              mandatory gate passes and a last-known-good checkpoint verifies.
            </p>
          </div>
        </div>
        {selfUpdate?.configured !== true && (
          <p className="notice" role="status">
            Configure the owner-only self-update state root to enable staged releases.
          </p>
        )}
        {selfUpdate?.configured === true && selfUpdate.status !== "ready" && (
          <p className="field-help" role="status">
            No fully validated update is awaiting activation. Production remains unchanged.
          </p>
        )}
        {selfUpdate?.configured === true && selfUpdate.status === "ready" && (
          <div className="issued-session" role="status" aria-live="polite">
            <strong>WORK STATION UPDATE READY</strong>
            <span>Version: {selfUpdate.version}</span>
            <span>
              What changed: candidate {selfUpdate.candidate_commit?.slice(0, 12)} passed
              the complete isolated release matrix.
            </span>
            <span>Benefits: validated release changes are ready without modifying production.</span>
            <span>Performance: {selfUpdate.gates.some((gate) => gate.name === "performance" && gate.passed) ? "PASS" : "NOT REPORTED"}</span>
            <span>Quality: {selfUpdate.gates.some((gate) => gate.name === "release" && gate.passed) ? "PASS" : "NOT REPORTED"}</span>
            <span>Security: {selfUpdate.gates.some((gate) => gate.name === "security" && gate.passed) ? "PASS" : "NOT REPORTED"}</span>
            <span>Compatibility: {selfUpdate.gates.some((gate) => gate.name === "routing_admission_hardware" && gate.passed) ? "PASS" : "NOT REPORTED"}</span>
            <span>Rollback checkpoint: {selfUpdate.rollback_ready ? "READY" : "NOT READY"}</span>
            <div className="settings-actions">
              <button
                type="button"
                className="button button-primary"
                disabled={updateBusy || !selfUpdate.checkpoint_ready || !selfUpdate.rollback_ready}
                onClick={() => void decideUpdate("update")}
              >
                UPDATE
              </button>
              <button
                type="button"
                className="button button-secondary"
                disabled={updateBusy}
                onClick={() => void decideUpdate("cancel")}
              >
                CANCEL
              </button>
            </div>
          </div>
        )}
        {updateNotice !== null && <p role="status">{updateNotice}</p>}
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
        {desktopRuntime && (
          <div className="desktop-preferences">
            <label className="desktop-preference-toggle">
              <input
                type="checkbox"
                checked={desktopAutostart}
                disabled={desktopBusy}
                onChange={(event) => void updateDesktopAutostart(event.target.checked)}
              />
              <span>Open WORK STATION when I sign in</span>
            </label>
            <div className="settings-section-heading">
              <p className="field-help">
                Native alerts: {desktopNotifications ? "enabled" : "not enabled"}
              </p>
              <button
                type="button"
                className="button button-secondary"
                disabled={desktopBusy || desktopNotifications}
                onClick={() => void enableDesktopNotifications()}
              >
                {desktopNotifications ? "Notifications enabled" : "Enable notifications"}
              </button>
            </div>
            {desktopNotice !== null && <p role="status">{desktopNotice}</p>}
          </div>
        )}
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
            {diagnostics.hardware !== undefined && diagnostics.hardware !== null && (
              <p className="field-help">
                Hardware profile: {diagnostics.hardware.profile_gib} GiB · {diagnostics.hardware.gpu_count} GPU
                {diagnostics.hardware.restart_required ? " · restart required" : " · active"}
                {diagnostics.hardware.runtime_validated ? " · runtime validated" : " · runtime validation pending"}
              </p>
            )}
            {(diagnostics.routes ?? []).map((route) => (
              <p className="field-help" key={route.task}>
                {route.task}: {route.model_id} · {route.inference_mode}
              </p>
            ))}
            {(diagnostics.external_providers ?? []).map((provider) => (
              <p className="field-help" key={provider.provider_id}>
                External {provider.provider_id}: {provider.status} · {provider.verified_model_count} verified model
                {provider.verified_model_count === 1 ? "" : "s"} · ${(provider.spent_micros / 1_000_000).toFixed(2)} spent
              </p>
            ))}
            {diagnostics.agents !== undefined && diagnostics.agents !== null && (
              <p className="field-help">
                Agents: {diagnostics.agents.active_count} active · {diagnostics.agents.retained_count} retained owner run
                {diagnostics.agents.retained_count === 1 ? "" : "s"}
              </p>
            )}
            {diagnostics.self_update !== undefined && (
              <p className="field-help">
                Update engine: {diagnostics.self_update.configured ? diagnostics.self_update.status : "unconfigured"}
                {diagnostics.self_update.rollback_ready ? " · rollback ready" : ""}
              </p>
            )}
            {(diagnostics.security_events ?? []).length > 0 && (
              <p className="field-help">
                Security containment events retained: {diagnostics.security_events?.length}
              </p>
            )}
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
