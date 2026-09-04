import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentOSCapabilities, AgentRun } from "../src/api/contracts";
import { AgentPanel } from "../src/features/agents/AgentPanel";


const kinds = [
  "planner", "coding", "debugging", "research", "browser", "data",
  "vision", "image", "voice", "rag", "automation", "verifier",
] as const;

const capabilities: AgentOSCapabilities = {
  profiles: kinds.map((kind) => ({
    kind,
    permissions: ["model_inference"],
    registered: !["image", "voice", "verifier"].includes(kind),
  })),
  max_retries: 2,
  max_deadline_seconds: 600,
  active_runs: 0,
  max_concurrency: 2,
  persistence: "bounded_process_memory",
  controls: ["pause", "resume", "approve", "modify", "retry"],
};

const queued: AgentRun = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  goal: "Diagnose the failing integration test.",
  source: "text",
  task: "debugging",
  specialist: "debugging",
  status: "queued",
  created_at: "2026-08-31T00:00:00Z",
  updated_at: "2026-08-31T00:00:00Z",
  output: null,
  failure_code: null,
  plan: [],
  events: [{
    sequence: 1,
    status: "queued",
    created_at: "2026-08-31T00:00:00Z",
    step_id: null,
    attempt: null,
    agent: null,
    model_id: null,
    action: "submitted",
    detail_sha256: null,
  }],
  attempts: [],
  pause_requested: false,
  requires_approval: false,
  approved: false,
  revision: 1,
  manual_retry_count: 0,
  can_pause: true,
  can_resume: false,
  can_approve: false,
  can_modify: true,
  can_retry: false,
};

describe("AgentPanel", () => {
  it("submits only the typed goal/task/specialist contract", async () => {
    const onCreate = vi.fn(async () => queued);
    render(
      <AgentPanel
        onClose={vi.fn()}
        onLoadCapabilities={vi.fn(async () => capabilities)}
        onLoadRuns={vi.fn(async () => [])}
        onCreate={onCreate}
        onCancel={vi.fn(async () => queued)}
        onControl={vi.fn(async () => queued)}
        onModify={vi.fn(async () => queued)}
      />,
    );

    expect(await screen.findByText(/0 active · 2 maximum concurrent/)).toBeVisible();
    await userEvent.type(screen.getByLabelText("Agent goal"), "Diagnose the failing integration test.");
    await userEvent.selectOptions(screen.getByLabelText("Task"), "debugging");
    await userEvent.selectOptions(screen.getByLabelText("Specialist"), "debugging");
    await userEvent.click(screen.getByRole("button", { name: "Run agent" }));

    expect(onCreate).toHaveBeenCalledWith({
      goal: "Diagnose the failing integration test.",
      task: "debugging",
      specialist: "debugging",
      max_retries: 1,
      deadline_seconds: 180,
      source: "text",
      require_owner_approval: false,
    });
    expect(await screen.findByText(/model-inference permission only/)).toBeVisible();
    expect(screen.getByText("queued")).toBeVisible();
    expect(screen.getByText("Diagnose the failing integration test.")).toBeVisible();
    expect(screen.getByRole("region", { name: "Live mission activity" })).toHaveTextContent(
      "submitted · queued",
    );
  });

  it("does not expose unregistered specialists", async () => {
    render(
      <AgentPanel
        onClose={vi.fn()}
        onLoadCapabilities={vi.fn(async () => capabilities)}
        onLoadRuns={vi.fn(async () => [])}
        onCreate={vi.fn(async () => queued)}
        onCancel={vi.fn(async () => queued)}
        onControl={vi.fn(async () => queued)}
        onModify={vi.fn(async () => queued)}
      />,
    );

    const specialist = await screen.findByLabelText("Specialist");
    expect(specialist).not.toHaveTextContent("verifier");
    expect(specialist).toHaveTextContent("coding");
  });

  it("exposes truthful owner approval and revision controls", async () => {
    const awaiting: AgentRun = {
      ...queued,
      status: "needs_approval",
      pause_requested: false,
      requires_approval: true,
      can_pause: false,
      can_approve: true,
      events: [{
        ...queued.events[0]!,
        status: "needs_approval",
        action: "approval_required",
      }],
    };
    const approved: AgentRun = {
      ...awaiting,
      status: "queued",
      approved: true,
      can_pause: true,
      can_approve: false,
    };
    const onControl = vi.fn(async () => approved);
    const onModify = vi.fn(async () => awaiting);
    render(
      <AgentPanel
        onClose={vi.fn()}
        onLoadCapabilities={vi.fn(async () => capabilities)}
        onLoadRuns={vi.fn(async () => [awaiting])}
        onCreate={vi.fn(async () => queued)}
        onCancel={vi.fn(async () => awaiting)}
        onControl={onControl}
        onModify={onModify}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(onControl).toHaveBeenCalledWith("approve", awaiting.id);
  });
});
