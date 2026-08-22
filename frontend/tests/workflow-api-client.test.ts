import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { jsonResponse, token } from "./fixtures";

const workflowId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const stepId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const pendingWorkflow = {
  id: workflowId,
  name: "Research project",
  status: "pending",
  step_count: 1,
  current_step_position: null,
  cancel_requested: false,
  result: null,
  error_code: null,
  created_at: "2026-08-22T00:00:00Z",
  updated_at: "2026-08-22T00:00:00Z",
  started_at: null,
  completed_at: null,
  steps: [
    {
      id: stepId,
      position: 1,
      tool_name: "memory_search",
      permission: "personal_memory_read",
      arguments: { query: "roadmap", limit: 8 },
      status: "pending",
      tool_execution_id: null,
      result: null,
      error_code: null,
      started_at: null,
      completed_at: null,
      duration_ms: null,
    },
  ],
};

const completedWorkflow = {
  ...pendingWorkflow,
  status: "completed",
  current_step_position: 1,
  result: { steps: [{ position: 1, result: { items: [] } }] },
  started_at: "2026-08-22T00:00:01Z",
  completed_at: "2026-08-22T00:00:02Z",
  steps: [
    {
      ...pendingWorkflow.steps[0],
      status: "completed",
      tool_execution_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      result: { items: [] },
      started_at: "2026-08-22T00:00:01Z",
      completed_at: "2026-08-22T00:00:02Z",
      duration_ms: 4,
    },
  ],
};

describe("workflow API client", () => {
  it("uses exact owner workflow endpoints and request bodies", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items: [pendingWorkflow] }))
      .mockResolvedValueOnce(jsonResponse(pendingWorkflow, 201))
      .mockResolvedValueOnce(jsonResponse(pendingWorkflow))
      .mockResolvedValueOnce(jsonResponse(pendingWorkflow, 202))
      .mockResolvedValueOnce(
        jsonResponse({
          ...pendingWorkflow,
          status: "cancelled",
          error_code: "cancelled",
          completed_at: "2026-08-22T00:00:01Z",
          steps: [
            {
              ...pendingWorkflow.steps[0],
              status: "cancelled",
              error_code: "cancelled",
              completed_at: "2026-08-22T00:00:01Z",
              duration_ms: 0,
            },
          ],
        }),
      );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const request = {
      name: "Research project",
      steps: [
        {
          tool_name: "memory_search",
          arguments: { query: "roadmap", limit: 8 },
        },
      ],
    };

    await expect(client.listWorkflows({ limit: 20 })).resolves.toEqual({
      items: [pendingWorkflow],
    });
    await expect(client.createWorkflow(request)).resolves.toEqual(pendingWorkflow);
    await expect(client.getWorkflow(workflowId)).resolves.toEqual(pendingWorkflow);
    await expect(client.startWorkflow(workflowId)).resolves.toEqual(pendingWorkflow);
    await expect(client.cancelWorkflow(workflowId)).resolves.toMatchObject({
      status: "cancelled",
    });

    expect(fetchImplementation.mock.calls.map((call) => call[0].toString())).toEqual([
      "http://127.0.0.1:8000/api/v1/workflows?limit=20",
      "http://127.0.0.1:8000/api/v1/workflows",
      `http://127.0.0.1:8000/api/v1/workflows/${workflowId}`,
      `http://127.0.0.1:8000/api/v1/workflows/${workflowId}/start`,
      `http://127.0.0.1:8000/api/v1/workflows/${workflowId}`,
    ]);
    expect(JSON.parse(String(fetchImplementation.mock.calls[1]?.[1]?.body))).toEqual(
      request,
    );
    expect(fetchImplementation.mock.calls[3]?.[1]?.method).toBe("POST");
    expect(fetchImplementation.mock.calls[4]?.[1]?.method).toBe("DELETE");
  });

  it("rejects inconsistent workflow lifecycle records", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse({
          ...completedWorkflow,
          completed_at: null,
        }),
      ) as typeof fetch,
    });

    await expect(client.getWorkflow(workflowId)).rejects.toMatchObject({
      kind: "unexpected",
    });
  });
});
