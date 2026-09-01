import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FeatureRegistry, ProductFeature } from "../src/api/contracts";
import { parseFeatureRegistry } from "../src/api/contracts";
import { FeatureCatalogPanel } from "../src/features/catalog/FeatureCatalogPanel";

function feature(index: number, overrides: Partial<ProductFeature> = {}): ProductFeature {
  return {
    id: `ai_presence.feature_${index}`,
    layer: "ai_presence",
    category: "Conversation",
    title: `Feature ${index}`,
    description: "An authenticated capability.",
    ui_entry_point: `/home#feature-${index}`,
    backend_capability: "conversations",
    required_permissions: ["owner_session"],
    dependencies: [],
    status: "implemented",
    test_coverage: ["web:feature_catalog"],
    ...overrides,
  };
}

function registry(): FeatureRegistry {
  const items = [
    ...Array.from({ length: 140 }, (_, index) => feature(index)),
    feature(140, {
      id: "universal_workspace.coding",
      layer: "universal_workspace",
      category: "Core workspaces",
      title: "Coding workspace",
      ui_entry_point: "/workspaces#coding",
      backend_capability: "conversation_and_agent_workspace",
    }),
    feature(141, {
      id: "universal_workspace.market_alerts",
      layer: "universal_workspace",
      category: "Market operations",
      title: "Market alerts",
      ui_entry_point: "/workspaces/finance#market-alerts",
      backend_capability: "market_workspace_roadmap",
      dependencies: ["market_workspace_persistence_and_simulation"],
      status: "planned",
      test_coverage: ["manual:documented_gap"],
    }),
  ];
  return { schema_version: 1, product: "AI OS", count: items.length, items };
}

describe("FeatureCatalogPanel", () => {
  it("loads modules lazily and only opens implemented capabilities", async () => {
    const snapshot = registry();
    const onOpen = vi.fn();
    render(
      <FeatureCatalogPanel
        layer="universal_workspace"
        onClose={vi.fn()}
        onLoad={vi.fn(async () => snapshot)}
        onOpen={onOpen}
      />,
    );

    expect(await screen.findByText(/142 registered product capabilities/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Open capability" }));
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: "universal_workspace.coding" }));
    expect(screen.getByRole("button", { name: "Documented boundary" })).toBeDisabled();
  });

  it("rejects duplicate or undersized machine-readable registries", () => {
    const snapshot = registry();
    expect(parseFeatureRegistry(snapshot)).toEqual(snapshot);
    expect(() => parseFeatureRegistry({ ...snapshot, count: 1 })).toThrow();
    expect(() => parseFeatureRegistry({ ...snapshot, items: snapshot.items.slice(0, 20), count: 20 })).toThrow();
  });

  it("filters the loaded module catalog without requesting execution", async () => {
    const onOpen = vi.fn();
    render(
      <FeatureCatalogPanel
        layer="universal_workspace"
        onClose={vi.fn()}
        onLoad={vi.fn(async () => registry())}
        onOpen={onOpen}
      />,
    );
    const search = await screen.findByRole("searchbox", { name: "Find a capability" });
    await userEvent.type(search, "market alerts");
    await waitFor(() => expect(screen.queryByText("Coding workspace")).not.toBeInTheDocument());
    expect(screen.getByText("Market alerts")).toBeVisible();
    expect(onOpen).not.toHaveBeenCalled();
  });
});
