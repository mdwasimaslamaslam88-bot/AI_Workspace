import type { FeatureLayer } from "../../api/contracts";

interface ProductLayerNavigationProps {
  activeLayer: FeatureLayer;
  onSelect: (layer: FeatureLayer) => void;
}

const layers: Array<{ id: FeatureLayer; label: string; shortLabel: string }> = [
  { id: "ai_presence", label: "AI Presence / Home", shortLabel: "Home" },
  { id: "mission_control", label: "Mission Control", shortLabel: "Missions" },
  { id: "universal_workspace", label: "Universal Workspace", shortLabel: "Workspace" },
  { id: "ai_command_center", label: "AI Command Center", shortLabel: "Command" },
  { id: "apps_hub", label: "Apps / Life / Business Hub", shortLabel: "Apps" },
];

export function ProductLayerNavigation({ activeLayer, onSelect }: ProductLayerNavigationProps) {
  return (
    <nav className="product-layer-navigation" aria-label="AI OS layers">
      {layers.map((layer) => (
        <button
          className={activeLayer === layer.id ? "product-layer active" : "product-layer"}
          type="button"
          aria-current={activeLayer === layer.id ? "page" : undefined}
          key={layer.id}
          onClick={() => onSelect(layer.id)}
        >
          <span>{layer.shortLabel}</span>
          <small>{layer.label}</small>
        </button>
      ))}
    </nav>
  );
}
