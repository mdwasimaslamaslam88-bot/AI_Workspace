import { describe, expect, it, vi } from "vitest";

import type { Workflow, WorkflowStatus } from "@work-station/shared";

import {
  isWorkflowTerminal,
  pollWorkflowUntilTerminal,
  WorkflowPollingTimeoutError,
} from "../src/workflows/monitor";

const workflowId = "11111111-1111-4111-8111-111111111111";

function workflow(status: WorkflowStatus): Workflow {
  return {
    id: workflowId,
    name: "Private workflow",
    status,
    step_count: 1,
    current_step_position: status === "running" ? 1 : null,
    cancel_requested: false,
    result: status === "completed" ? { ok: true } : null,
    error_code: ["failed", "cancelled", "timed_out"].includes(status)
      ? `workflow_${status}`
      : null,
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
    started_at: status === "pending" ? null : "2026-08-23T00:00:00Z",
    completed_at: isWorkflowTerminal(status) ? "2026-08-23T00:00:01Z" : null,
    steps: [],
  };
}

describe("mobile workflow completion monitor", () => {
  it("reports completion only after a server-authoritative terminal state", async () => {
    const responses = [workflow("running"), workflow("running"), workflow("completed")];
    const get = vi.fn(async () => responses.shift()!);
    const updates: Workflow[] = [];

    const result = await pollWorkflowUntilTerminal(
      workflowId,
      new AbortController().signal,
      get,
      (item) => updates.push(item),
      { attempts: 3, intervalMilliseconds: 0, wait: async () => true },
    );

    expect(result?.status).toBe("completed");
    expect(updates.map((item) => item.status)).toEqual([
      "running",
      "running",
      "completed",
    ]);
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("stops silently on owner cancellation without inventing a terminal result", async () => {
    const controller = new AbortController();
    const get = vi.fn(async () => {
      controller.abort();
      return workflow("running");
    });
    const update = vi.fn();

    await expect(pollWorkflowUntilTerminal(
      workflowId,
      controller.signal,
      get,
      update,
    )).resolves.toBeNull();
    expect(update).not.toHaveBeenCalled();
  });

  it("fails explicitly when the bounded deadline cannot confirm completion", async () => {
    await expect(pollWorkflowUntilTerminal(
      workflowId,
      new AbortController().signal,
      async () => workflow("running"),
      vi.fn(),
      { attempts: 2, intervalMilliseconds: 0, wait: async () => true },
    )).rejects.toBeInstanceOf(WorkflowPollingTimeoutError);
  });
});
