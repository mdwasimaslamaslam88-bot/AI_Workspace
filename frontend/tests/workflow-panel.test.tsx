import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Workflow, WorkflowStep } from "../src/api/contracts";
import { WorkflowPanel } from "../src/features/workflows/WorkflowPanel";
import { rawSecret } from "./fixtures";

const createdAt = "2026-08-22T00:00:00Z";

function step(position: number, toolName: string, permission: string): WorkflowStep {
  return {
    id: `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa${position}`,
    position,
    tool_name: toolName,
    permission,
    arguments: { query: "release readiness", limit: 4 },
    status: "pending",
    tool_execution_id: null,
    result: null,
    error_code: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
  };
}

const pending: Workflow = {
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  name: "Release research",
  status: "pending",
  step_count: 3,
  current_step_position: null,
  cancel_requested: false,
  result: null,
  error_code: null,
  created_at: createdAt,
  updated_at: createdAt,
  started_at: null,
  completed_at: null,
  steps: [
    step(1, "document_search", "personal_documents_read"),
    step(2, "memory_search", "personal_memory_read"),
    step(3, "conversation_search", "personal_conversations_read"),
  ],
};

const running: Workflow = {
  ...pending,
  status: "running",
  current_step_position: 1,
  started_at: "2026-08-22T00:00:01Z",
  steps: [
    {
      ...pending.steps[0],
      status: "running",
      started_at: "2026-08-22T00:00:01Z",
    },
    ...pending.steps.slice(1),
  ],
};

const completed: Workflow = {
  ...running,
  status: "completed",
  current_step_position: 3,
  result: { step_count: 3 },
  completed_at: "2026-08-22T00:00:02Z",
  steps: pending.steps.map((item) => ({
    ...item,
    status: "completed",
    tool_execution_id: `cccccccc-cccc-4ccc-8ccc-ccccccccccc${item.position}`,
    result: { items: [] },
    started_at: "2026-08-22T00:00:01Z",
    completed_at: "2026-08-22T00:00:02Z",
    duration_ms: 2,
  })),
};

function props() {
  return {
    onClose: vi.fn(),
    onLoad: vi.fn(async () => [] as Workflow[]),
    onCreate: vi.fn(async () => pending),
    onStart: vi.fn(async () => running),
    onGet: vi.fn(async (
      _workflowId: string,
      _signal?: AbortSignal,
    ) => {
      void _workflowId;
      void _signal;
      return completed;
    }),
    onCancel: vi.fn(async () => ({
      ...pending,
      status: "cancelled" as const,
      error_code: "cancelled",
      completed_at: "2026-08-22T00:00:01Z",
    })),
  };
}

describe("WorkflowPanel", () => {
  it("composes fixed owner-scoped tools, starts the task, and renders progress", async () => {
    const actions = props();
    render(<WorkflowPanel {...actions} />);

    await screen.findByText("No workflows yet.");
    await userEvent.type(screen.getByLabelText(/Task name/), "Release research");
    await userEvent.type(screen.getByLabelText("Research goal"), "release readiness");
    await userEvent.click(screen.getByRole("button", { name: "Run workflow" }));

    await waitFor(() => expect(actions.onGet).toHaveBeenCalledWith(
      pending.id,
      expect.any(AbortSignal),
    ));
    expect(actions.onCreate).toHaveBeenCalledWith(
      {
        name: "Release research",
        steps: [
          {
            tool_name: "document_search",
            arguments: { query: "release readiness", limit: 4 },
          },
          {
            tool_name: "memory_search",
            arguments: { query: "release readiness", limit: 8 },
          },
          {
            tool_name: "conversation_search",
            arguments: { query: "release readiness", limit: 10 },
          },
        ],
      },
      expect.any(AbortSignal),
    );
    expect(actions.onStart).toHaveBeenCalledWith(
      pending.id,
      expect.any(AbortSignal),
    );
    expect(await screen.findAllByText("completed")).toHaveLength(4);
    expect(screen.getByText("Step 3 of 3")).toBeVisible();
    expect(screen.getAllByText(/"items": \[\]/)).toHaveLength(3);
  });

  it("cancels a pending task using its opaque identity", async () => {
    const actions = props();
    actions.onLoad.mockResolvedValueOnce([pending]);
    render(<WorkflowPanel {...actions} />);

    await screen.findByText("Release research");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(actions.onCancel).toHaveBeenCalledWith(
      pending.id,
      expect.any(AbortSignal),
    ));
    expect(await screen.findByText("cancelled")).toBeVisible();
  });

  it("refreshes every running task restored from owner history", async () => {
    const actions = props();
    const second = {
      ...running,
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      name: "Second task",
    };
    actions.onLoad.mockResolvedValueOnce([running, second]);
    actions.onGet.mockImplementation(async (workflowId) => ({
      ...completed,
      id: workflowId,
      name: workflowId === running.id ? running.name : second.name,
    }));
    render(<WorkflowPanel {...actions} />);

    await waitFor(() => {
      expect(actions.onGet).toHaveBeenCalledWith(
        running.id,
        expect.any(AbortSignal),
      );
      expect(actions.onGet).toHaveBeenCalledWith(
        second.id,
        expect.any(AbortSignal),
      );
    });
    expect(await screen.findByText("Second task")).toBeVisible();
    expect(screen.getAllByText("completed")).toHaveLength(8);
  });

  it("does not expose private error details", async () => {
    const actions = props();
    actions.onCreate.mockRejectedValueOnce(new Error(rawSecret));
    render(<WorkflowPanel {...actions} />);

    await screen.findByText("No workflows yet.");
    await userEvent.type(screen.getByLabelText("Research goal"), "private failure");
    await userEvent.click(screen.getByRole("button", { name: "Run workflow" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Workflow could not be created or started.",
    );
    expect(document.body.textContent).not.toContain(rawSecret);
  });
});
