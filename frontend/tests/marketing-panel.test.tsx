import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Connector, MarketingCampaign, MarketingStageKind } from "../src/api/contracts";
import { MarketingPanel } from "../src/features/marketing/MarketingPanel";
import { rawSecret } from "./fixtures";

const kinds: MarketingStageKind[] = ["research", "strategy", "content", "creative", "approval", "publish", "analytics", "optimization"];

function record(status: MarketingCampaign["status"] = "pending"): MarketingCampaign {
  const currentStage = status === "needs_approval" ? "approval" : status === "awaiting_analytics" ? "analytics" : null;
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    name: "Verified launch",
    objective: "Launch truthfully.",
    product: "AI OS",
    audience: "Technical founders",
    channels: ["email"],
    source_facts: [{ source_reference: "brief.md#L1", fact: "AI OS is local-first." }],
    publisher_connector_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    publish_path: "/v1/campaigns",
    status,
    current_stage: currentStage,
    analytics: null,
    error_code: null,
    created_at: "2026-09-02T00:00:00Z",
    updated_at: "2026-09-02T00:00:00Z",
    started_at: status === "pending" ? null : "2026-09-02T00:00:01Z",
    approved_at: status === "awaiting_analytics" ? "2026-09-02T00:00:02Z" : null,
    published_at: status === "awaiting_analytics" ? "2026-09-02T00:00:03Z" : null,
    completed_at: null,
    stages: kinds.map((kind, index) => ({
      id: `bbbbbbbb-bbbb-4bbb-8bbb-${String(index + 1).padStart(12, "0")}`,
      position: index + 1,
      kind,
      status: index < 4 && status !== "pending" ? "completed" : kind === "approval" && status === "needs_approval" ? "blocked" : "pending",
      output: index < 4 && status !== "pending" ? `Verified ${kind}` : null,
      output_sha256: index < 4 && status !== "pending" ? "a".repeat(64) : null,
      model_id: index < 4 && status !== "pending" ? "local/model" : null,
      connector_execution_id: null,
      error_code: null,
      started_at: index < 4 && status !== "pending" ? "2026-09-02T00:00:01Z" : null,
      completed_at: index < 4 && status !== "pending" ? "2026-09-02T00:00:02Z" : null,
      duration_ms: index < 4 && status !== "pending" ? 10 : null,
    })),
  };
}

const connector: Connector = {
  id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  name: "Publisher",
  provider: "Example",
  service: "Publisher",
  kind: "rest",
  base_url: "https://publisher.example.test",
  auth_kind: "none",
  credential_configured: false,
  scopes: ["read", "write"],
  permissions: ["read", "write"],
  capabilities: ["publish"],
  path_prefixes: ["/v1/"],
  health_path: "/v1/health",
  discovery_path: null,
  enabled: true,
  connection_status: "ready",
  timeout_seconds: 2,
  max_retries: 0,
  rate_limit_requests_per_minute: 30,
  last_health_checked_at: null,
  last_successful_test_at: null,
  audit_reference: null,
  created_at: "2026-09-02T00:00:00Z",
  updated_at: "2026-09-02T00:00:00Z",
  revoked_at: null,
};

function props(campaigns: MarketingCampaign[] = []) {
  return {
    onClose: vi.fn(),
    onLoad: vi.fn(async () => campaigns),
    onLoadConnectors: vi.fn(async () => [connector]),
    onCreate: vi.fn(async () => record()),
    onGet: vi.fn(async () => record("needs_approval")),
    onStart: vi.fn(async () => record("pending")),
    onApprove: vi.fn(async () => record("awaiting_analytics")),
    onAnalytics: vi.fn(async () => ({ ...record("completed"), completed_at: "2026-09-02T00:00:04Z", analytics: { ctr_percent: "10.00" } })),
    onCancel: vi.fn(async () => ({ ...record("cancelled"), completed_at: "2026-09-02T00:00:04Z", error_code: "campaign_cancelled" })),
  };
}

describe("MarketingPanel", () => {
  it("creates a grounded campaign with an explicit publisher contract", async () => {
    const actions = props();
    render(<MarketingPanel {...actions} />);
    await screen.findByText("No campaigns yet.");
    await userEvent.type(screen.getByLabelText("Campaign name"), "Verified launch");
    await userEvent.type(screen.getByLabelText("Objective"), "Launch truthfully.");
    await userEvent.type(screen.getByLabelText("Product"), "AI OS");
    await userEvent.type(screen.getByLabelText("Audience"), "Technical founders");
    await userEvent.type(screen.getByLabelText("Source reference"), "brief.md#L1");
    await userEvent.type(screen.getByLabelText("Source fact"), "AI OS is local-first.");
    await userEvent.selectOptions(screen.getByLabelText("Publisher (optional)"), connector.id);
    await userEvent.type(screen.getByLabelText("Authorized publish path (optional)"), "/v1/campaigns");
    await userEvent.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(actions.onCreate).toHaveBeenCalledWith({
      name: "Verified launch",
      objective: "Launch truthfully.",
      product: "AI OS",
      audience: "Technical founders",
      channels: ["email"],
      source_facts: [{ source_reference: "brief.md#L1", fact: "AI OS is local-first." }],
      publisher_connector_id: connector.id,
      publish_path: "/v1/campaigns",
    }, expect.any(AbortSignal));
  });

  it("shows real stage state and requires a direct publish approval", async () => {
    const actions = props([record("needs_approval")]);
    render(<MarketingPanel {...actions} />);
    await screen.findByText("needs approval");
    expect(screen.getAllByText("completed")).toHaveLength(4);
    await userEvent.click(screen.getByRole("button", { name: "Approve & publish" }));
    await waitFor(() => expect(actions.onApprove).toHaveBeenCalledWith(
      record().id, expect.any(AbortSignal),
    ));
    expect(await screen.findByText("awaiting analytics")).toBeVisible();
  });

  it("does not render private backend errors", async () => {
    const actions = props([record("needs_approval")]);
    actions.onApprove.mockRejectedValueOnce(new Error(rawSecret));
    render(<MarketingPanel {...actions} />);
    await userEvent.click(await screen.findByRole("button", { name: "Approve & publish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rejected or could not be verified");
    expect(document.body.textContent).not.toContain(rawSecret);
  });
});
