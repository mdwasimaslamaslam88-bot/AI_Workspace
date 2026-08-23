import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ProductCapability,
  ProductCapabilityId,
  ProductCapabilityReason,
  SystemDiagnostics,
} from "../../api/contracts";

interface SettingsPanelProps {
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<ProductCapability[]>;
  onLoadDiagnostics: (signal?: AbortSignal) => Promise<SystemDiagnostics>;
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
  onManageMemory,
}: SettingsPanelProps) {
  const [capabilities, setCapabilities] = useState<ProductCapability[]>([]);
  const [diagnostics, setDiagnostics] = useState<SystemDiagnostics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError(null);
    try {
      const [items, diagnosticSnapshot] = await Promise.all([
        onLoad(current.signal),
        onLoadDiagnostics(current.signal),
      ]);
      if (!current.signal.aborted) {
        setCapabilities(items);
        setDiagnostics(diagnosticSnapshot);
      }
    } catch {
      if (!current.signal.aborted) {
        setError("Capability diagnostics could not be loaded.");
      }
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, [onLoad, onLoadDiagnostics]);

  useEffect(() => {
    void load();
    return () => controller.current?.abort();
  }, [load]);

  const available = capabilities.filter(
    (capability) => capability.status === "available",
  ).length;

  return (
    <aside className="settings-panel" aria-labelledby="settings-panel-title">
      <header className="settings-panel-header">
        <div>
          <p className="eyebrow">Local product state</p>
          <h2 id="settings-panel-title">Settings & diagnostics</h2>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>
          Close
        </button>
      </header>

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
