import { useEffect, useMemo, useState } from "react";

import type { FeatureLayer, FeatureRegistry, ProductFeature } from "../../api/contracts";

interface FeatureCatalogPanelProps {
  layer: Extract<FeatureLayer, "universal_workspace" | "apps_hub">;
  onClose: () => void;
  onLoad: (signal?: AbortSignal) => Promise<FeatureRegistry>;
  onOpen: (feature: ProductFeature) => void;
}

const statusLabels = {
  implemented: "Ready",
  runtime_dependent: "Runtime gated",
  external_dependency: "Connect service",
  planned: "Documented boundary",
} as const;

export function FeatureCatalogPanel({ layer, onClose, onLoad, onOpen }: FeatureCatalogPanelProps) {
  const [registry, setRegistry] = useState<FeatureRegistry | null>(null);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void onLoad(controller.signal)
      .then((loaded) => {
        setRegistry(loaded);
        setNotice(null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setNotice("The authenticated feature registry could not be loaded.");
      });
    return () => controller.abort();
  }, [onLoad]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (registry?.items ?? []).filter((feature) =>
      feature.layer === layer &&
      (normalized.length === 0 ||
        [feature.title, feature.category, feature.description].some((value) =>
          value.toLowerCase().includes(normalized),
        )),
    );
  }, [layer, query, registry]);

  const categories = useMemo(() => {
    const grouped = new Map<string, ProductFeature[]>();
    for (const feature of filtered) {
      grouped.set(feature.category, [...(grouped.get(feature.category) ?? []), feature]);
    }
    return [...grouped.entries()];
  }, [filtered]);

  const title = layer === "universal_workspace" ? "Universal Workspace" : "Apps / Life / Business Hub";
  return (
    <aside className="feature-catalog-panel" aria-labelledby="feature-catalog-title">
      <header className="feature-catalog-header">
        <div>
          <p className="eyebrow">DYNAMIC MODULE CATALOG</p>
          <h2 id="feature-catalog-title">{title}</h2>
          <p className="muted">Only real local capabilities open directly. External and planned boundaries stay explicit.</p>
        </div>
        <button type="button" className="button button-quiet" onClick={onClose}>Close</button>
      </header>
      <label className="feature-search">
        Find a capability
        <input
          type="search"
          value={query}
          placeholder="Search modules"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {notice !== null && <p className="notice notice-error" role="alert">{notice}</p>}
      {registry === null && notice === null && <p className="muted" role="status">Loading authenticated registry…</p>}
      {registry !== null && (
        <p className="feature-count" role="status">
          {filtered.length} visible in this layer · {registry.count} registered product capabilities
        </p>
      )}
      <div className="feature-category-list">
        {categories.map(([category, features]) => (
          <section className="feature-category" key={category}>
            <h3>{category}</h3>
            <div className="feature-card-grid">
              {features.map((feature) => {
                const canOpen = feature.status === "implemented" || feature.status === "runtime_dependent";
                return (
                  <article className="feature-card" id={feature.id.split(".")[1]?.replaceAll("_", "-")} key={feature.id}>
                    <div className="feature-card-heading">
                      <h4>{feature.title}</h4>
                      <span className={`feature-status feature-status-${feature.status}`}>
                        {statusLabels[feature.status]}
                      </span>
                    </div>
                    <p>{feature.description}</p>
                    {feature.dependencies.length > 0 && (
                      <p className="feature-dependencies">
                        Requires: {feature.dependencies.join(", ").replaceAll("_", " ")}
                      </p>
                    )}
                    <button
                      type="button"
                      className="button button-secondary"
                      disabled={!canOpen}
                      onClick={() => onOpen(feature)}
                    >
                      {canOpen ? "Open capability" : statusLabels[feature.status]}
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </aside>
  );
}
