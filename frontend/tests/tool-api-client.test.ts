import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { conversation, jsonResponse, token } from "./fixtures";

const descriptor = {
  name: "calculator",
  description: "Bounded arithmetic.",
  input_schema: {
    additionalProperties: false,
    properties: { expression: { type: "string" } },
    required: ["expression"],
    type: "object",
  },
  permission: "utility",
  timeout_seconds: 1,
  max_output_characters: 1024,
};

const execution = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  conversation_id: conversation.id,
  tool_name: "calculator",
  permission: "utility",
  status: "completed",
  initiator: "explicit_user",
  arguments: { expression: "6*7" },
  result: { value: 42 },
  error_code: null,
  started_at: "2026-08-22T00:00:00Z",
  completed_at: "2026-08-22T00:00:00Z",
  duration_ms: 1,
};


describe("tool API client", () => {
  it("loads registry and owner history with strict bounded response decoding", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ items: [descriptor] }))
      .mockResolvedValueOnce(jsonResponse({ items: [execution] }));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.listTools()).resolves.toEqual({ items: [descriptor] });
    await expect(client.listToolExecutions({ limit: 20 })).resolves.toEqual({
      items: [execution],
    });
    expect(fetchImplementation.mock.calls[1]?.[0].toString()).toContain(
      "/api/v1/tools/executions?limit=20",
    );
  });

  it("sends only the named tool, exact arguments, and optional owned context", async () => {
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(input.toString()).toBe(
          "http://127.0.0.1:8000/api/v1/tools/calculator/executions",
        );
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        expect(JSON.parse(String(init?.body))).toEqual({
          arguments: { expression: "6*7" },
          conversation_id: conversation.id,
        });
        return jsonResponse(execution, 201);
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(
      client.executeTool("calculator", {
        arguments: { expression: "6*7" },
        conversation_id: conversation.id,
      }),
    ).resolves.toEqual(execution);
  });

  it("rejects inconsistent terminal records instead of fabricating tool success", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse({
          ...execution,
          status: "completed",
          result: null,
        }),
      ) as typeof fetch,
    });

    await expect(
      client.executeTool("calculator", { arguments: { expression: "6*7" } }),
    ).rejects.toMatchObject({ kind: "unexpected" });
  });
});
