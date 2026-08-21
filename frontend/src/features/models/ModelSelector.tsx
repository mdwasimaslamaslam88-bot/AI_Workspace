import type { LocalModel } from "../../api/contracts";
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
      {selectedModelId !== null && (
        <div className="capability-row" aria-label="Selected model capabilities">
          {selectable
            .find((model) => model.model_id === selectedModelId)
            ?.capabilities.map((capability) => (
              <span className="capability" key={capability}>
                {capability.replaceAll("_", " ")}
              </span>
            ))}
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
    </section>
  );
}
