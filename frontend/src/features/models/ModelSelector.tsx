import {
  MODEL_READINESS_LABELS,
  modelContextLabel,
  modelHardwareLabel,
  modelReadiness,
  modelScaleLabel,
  type LocalModel,
} from "../../api/contracts";
import {
  modelSupportsVision,
  selectableTextModels,
} from "../../app/collections";

interface ModelSelectorProps {
  models: LocalModel[];
  selectedModelId: string | null;
  loading: boolean;
  error: string | null;
  disabled?: boolean;
  onSelect: (modelId: string) => void;
  onReload: () => void;
}

export function ModelSelector({
  models,
  selectedModelId,
  loading,
  error,
  disabled = false,
  onSelect,
  onReload,
}: ModelSelectorProps) {
  const selectable = selectableTextModels(models);
  const selected = models.find((model) => model.model_id === selectedModelId) ?? null;

  return (
    <section className="model-panel" aria-labelledby="model-label">
      <div>
        <label id="model-label" htmlFor="model-select">Local model</label>
        <select
          id="model-select"
          value={selectedModelId ?? ""}
          disabled={disabled || loading || selectable.length === 0}
          onChange={(event) => onSelect(event.target.value)}
        >
          {selectable.length === 0 && <option value="">No text model available</option>}
          {selectable.map((model) => (
            <option key={model.model_id} value={model.model_id}>
              {model.display_name}
              {model.parameter_class ? ` · ${model.parameter_class}` : ""}
              {modelSupportsVision(model) ? " · Vision" : ""}
            </option>
          ))}
        </select>
      </div>
      {selected !== null && (
        <div className="selected-model-summary">
          <div className="model-facts" aria-label="Selected model details">
            <span>{MODEL_READINESS_LABELS[modelReadiness(selected)]}</span>
            <span>{selected.runtime_id}</span>
            <span>{modelScaleLabel(selected.scale_class)}</span>
            <span>{modelContextLabel(selected.context_window)}</span>
            <span>{modelHardwareLabel(selected.hardware_class)}</span>
          </div>
          <div className="capability-row" aria-label="Selected model capabilities">
            {selected.capabilities.map((capability) => (
              <span className="capability" key={capability}>
                {capability.replaceAll("_", " ")}
              </span>
            ))}
          </div>
        </div>
      )}
      {loading && <p className="field-help" role="status">Discovering local models…</p>}
      {!loading && error !== null && (
        <div className="inline-error" role="alert">
          <span>{error}</span>
          <button className="button button-quiet" onClick={onReload}>Retry</button>
        </div>
      )}
      {!loading && error === null && selectable.length === 0 && (
        <p className="field-help">
          Configure and allowlist a local text-generation model in the backend.
        </p>
      )}
      {!loading && error === null && models.length > 0 && (
        <details className="model-catalog">
          <summary>Model catalog · {models.length} discovered</summary>
          <ul>
            {models.map((model) => {
              const readiness = modelReadiness(model);
              return (
                <li key={model.model_id}>
                  <div className="model-catalog-heading">
                    <strong>{model.display_name}</strong>
                    <span className={`model-readiness model-readiness-${readiness}`}>
                      {MODEL_READINESS_LABELS[readiness]}
                    </span>
                  </div>
                  <div className="model-facts">
                    <span>{model.installed ? "Installed" : "Not installed"}</span>
                    <span>{model.runtime_id}</span>
                    <span>{model.modality}</span>
                    <span>{modelScaleLabel(model.scale_class)}</span>
                    <span>{modelContextLabel(model.context_window)}</span>
                    <span>{modelHardwareLabel(model.hardware_class)}</span>
                  </div>
                  <div className="capability-row" aria-label={`${model.display_name} capabilities`}>
                    {model.capabilities.map((capability) => (
                      <span className="capability" key={capability}>
                        {capability.replaceAll("_", " ")}
                      </span>
                    ))}
                  </div>
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </section>
  );
}
