import { describe, expect, it, vi } from "vitest";

import {
  createIdempotencyKey,
  MobileApiClient,
  MobileApiError,
  normalizeMobileApiBaseUrl,
} from "../src/api/client";

const user = {
  id: "11111111-1111-4111-8111-111111111111",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const timestamp = "2026-01-01T00:00:00Z";
const conversationId = "22222222-2222-4222-8222-222222222222";
const asset = {
  id: "33333333-3333-4333-8333-333333333333",
  original_filename: "private-output.png",
  media_type: "image/png",
  byte_size: 4,
  content_sha256: "a".repeat(64),
  provenance_kind: "image_generation",
  source_asset_id: null,
  runtime_id: "private-runtime",
  model_id: "private-model",
  created_at: timestamp,
  deleted_at: null,
};

describe("mobile API client", () => {
  it("accepts only credential-free HTTP origins", () => {
    expect(normalizeMobileApiBaseUrl("https://work-station.example.ts.net")).toBe(
      "https://work-station.example.ts.net",
    );
    expect(() => normalizeMobileApiBaseUrl("https://token@example.test/api"))
      .toThrow("without credentials or a path");
    expect(() => normalizeMobileApiBaseUrl("http://192.0.2.1:8000"))
      .toThrow("must use HTTPS");
  });

  it("creates UUID idempotency keys accepted by bounded media routes", () => {
    const keys = Array.from({ length: 32 }, () => createIdempotencyKey());
    expect(new Set(keys)).toHaveLength(keys.length);
    for (const key of keys) {
      expect(key).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    }
  });

  it("uploads owner media with a backend-compatible UUID header", async () => {
    const fetchMock = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      expect(new Headers(init?.headers).get("Idempotency-Key")).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mobile-session");
      return new Response(JSON.stringify({
        ...asset,
        original_filename: "owner-upload.png",
        provenance_kind: "upload",
        runtime_id: null,
        model_id: null,
      }));
    });
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.uploadAsset({
      uri: "file:///private-cache/owner-upload.png",
      name: "owner-upload.png",
      mimeType: "image/png",
    })).resolves.toMatchObject({ provenance_kind: "upload" });
  });

  it("authenticates /users/me without exposing the bearer in the URL", async () => {
    const secret = "mobile-test-secret";
    const fetchMock = vi.fn(async (_input: URL | RequestInfo, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${secret}`);
      return new Response(JSON.stringify(user), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    const client = new MobileApiClient(secret, {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.getCurrentUser()).resolves.toEqual(user);
    const calledUrl = String(fetchMock.mock.calls[0]?.[0]);
    expect(calledUrl).toBe("https://work-station.example.ts.net/api/v1/users/me");
    expect(calledUrl).not.toContain(secret);
  });

  it("redacts backend error bodies and keeps only safe status metadata", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ error: { message: "private runtime and filesystem detail" } }),
        { status: 503, headers: { "X-Request-ID": "safe-request-id" } },
      ),
    );
    const client = new MobileApiClient("test-token", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.getCurrentUser()).rejects.toEqual(
      new MobileApiError(
        "unavailable",
        "The Personal AI is temporarily unavailable.",
        503,
        "safe-request-id",
      ),
    );
  });

  it("maps transport failures to a safe reconnectable state", async () => {
    const client = new MobileApiClient("test-token", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: vi.fn(async () => {
        throw new Error("private network detail");
      }),
    });

    await expect(client.getCurrentUser()).rejects.toEqual(
      new MobileApiError("network", "Could not reach WORK STATION."),
    );
  });

  it("loads private diagnostics and rotates a credential only in headers/body", async () => {
    const rotatedToken = "z".repeat(43);
    const diagnostics = {
      mode: "remote",
      services: [
        "backend",
        "database",
        "redis",
        "ollama",
        "vision",
        "image_runtime",
        "speech_to_text",
        "text_to_speech",
        "storage",
        "remote_gateway",
        "gpu",
      ].map((id) => ({ id, status: "ready" })),
      gpus: [],
    };
    const fetchMock = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        expect(url).not.toContain("mobile-session");
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer mobile-session",
        );
        return url.endsWith("/diagnostics")
          ? new Response(JSON.stringify(diagnostics), { status: 200 })
          : new Response(
              JSON.stringify({ access_token: rotatedToken, token_type: "bearer" }),
              { status: 200 },
            );
      },
    );
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.getSystemDiagnostics()).resolves.toEqual(diagnostics);
    await expect(client.rotateAccessToken()).resolves.toEqual({
      access_token: rotatedToken,
      token_type: "bearer",
    });
  });

  it("renames and deletes through the owner-scoped conversation route", async () => {
    const conversation = {
      id: "22222222-2222-4222-8222-222222222222",
      title: "Mobile chat",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchMock = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer mobile-session",
        );
        calls.push({
          url: input.toString(),
          method: init?.method ?? "GET",
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return init?.method === "DELETE"
          ? new Response(null, { status: 204 })
          : new Response(JSON.stringify(conversation), { status: 200 });
      },
    );
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(
      client.renameConversation(conversation.id, { title: "Mobile chat" }),
    ).resolves.toEqual(conversation);
    await expect(client.deleteConversation(conversation.id)).resolves.toBeUndefined();
    expect(calls.map(({ method, body }) => ({ method, body }))).toEqual([
      { method: "PATCH", body: { title: "Mobile chat" } },
      { method: "DELETE", body: undefined },
    ]);
    expect(calls.every(({ url }) => url.endsWith(`/api/v1/conversations/${conversation.id}`))).toBe(true);
  });

  it("uses owner-scoped memory, tool, and workflow contracts", async () => {
    const memory = {
      id: "44444444-4444-4444-8444-444444444444",
      category: "preference",
      state: "active",
      content: "Prefer concise mobile responses.",
      provenance_kind: "explicit_user_entry",
      created_at: timestamp,
      updated_at: timestamp,
      deleted_at: null,
    };
    const tool = {
      name: "calculator",
      description: "Evaluate bounded arithmetic.",
      input_schema: { type: "object" },
      permission: "calculation",
      timeout_seconds: 1,
      max_output_characters: 1024,
    };
    const execution = {
      id: "55555555-5555-4555-8555-555555555555",
      conversation_id: null,
      tool_name: tool.name,
      permission: tool.permission,
      status: "completed",
      initiator: "explicit_user",
      arguments: { expression: "2+2" },
      result: { value: 4 },
      error_code: null,
      started_at: timestamp,
      completed_at: timestamp,
      duration_ms: 2,
    };
    const workflow = {
      id: "66666666-6666-4666-8666-666666666666",
      name: "Mobile calculation",
      status: "pending",
      step_count: 1,
      current_step_position: null,
      cancel_requested: false,
      result: null,
      error_code: null,
      created_at: timestamp,
      updated_at: timestamp,
      started_at: null,
      completed_at: null,
      steps: [{
        id: "77777777-7777-4777-8777-777777777777",
        position: 1,
        tool_name: tool.name,
        permission: tool.permission,
        arguments: { expression: "2+2" },
        status: "pending",
        tool_execution_id: null,
        result: null,
        error_code: null,
        started_at: null,
        completed_at: null,
        duration_ms: null,
      }],
    };
    const calls: Array<{ path: string; method: string }> = [];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const url = new URL(input.toString());
      const method = init?.method ?? "GET";
      calls.push({ path: `${url.pathname}${url.search}`, method });
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer mobile-session");
      if (url.pathname.endsWith("/memories/settings")) {
        return new Response(JSON.stringify({ enabled: method === "PUT" ? false : true, created_at: timestamp, updated_at: timestamp }));
      }
      if (url.pathname.endsWith("/memories")) return new Response(JSON.stringify({ items: [memory] }));
      if (url.pathname.includes(`/memories/${memory.id}`)) {
        return new Response(JSON.stringify({ ...memory, state: "deleted", content: null, deleted_at: timestamp }));
      }
      if (url.pathname.endsWith("/tools")) return new Response(JSON.stringify({ items: [tool] }));
      if (url.pathname.endsWith("/executions")) return new Response(JSON.stringify(method === "GET" ? { items: [execution] } : execution));
      if (url.pathname.endsWith("/workflows")) return new Response(JSON.stringify(method === "GET" ? { items: [workflow] } : workflow));
      return new Response(JSON.stringify(workflow));
    });
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await expect(client.listMemories({ includeDeleted: true })).resolves.toEqual({ items: [memory] });
    await expect(client.getMemorySetting()).resolves.toMatchObject({ enabled: true });
    await expect(client.updateMemorySetting(false)).resolves.toMatchObject({ enabled: false });
    await expect(client.forgetMemory(memory.id)).resolves.toMatchObject({ state: "deleted", content: null });
    await expect(client.listTools()).resolves.toEqual({ items: [tool] });
    await expect(client.listToolExecutions({ limit: 10 })).resolves.toEqual({ items: [execution] });
    await expect(client.executeTool(tool.name, { arguments: { expression: "2+2" } })).resolves.toEqual(execution);
    await expect(client.listWorkflows({ limit: 10 })).resolves.toEqual({ items: [workflow] });
    await expect(client.createWorkflow({ name: workflow.name, steps: [{ tool_name: tool.name, arguments: { expression: "2+2" } }] })).resolves.toEqual(workflow);
    await expect(client.startWorkflow(workflow.id)).resolves.toEqual(workflow);
    await expect(client.cancelWorkflow(workflow.id)).resolves.toEqual(workflow);

    expect(calls).toEqual([
      { path: "/api/v1/memories?include_deleted=true", method: "GET" },
      { path: "/api/v1/memories/settings", method: "GET" },
      { path: "/api/v1/memories/settings", method: "PUT" },
      { path: `/api/v1/memories/${memory.id}`, method: "DELETE" },
      { path: "/api/v1/tools", method: "GET" },
      { path: "/api/v1/tools/executions?limit=10", method: "GET" },
      { path: "/api/v1/tools/calculator/executions", method: "POST" },
      { path: "/api/v1/workflows?limit=10", method: "GET" },
      { path: "/api/v1/workflows", method: "POST" },
      { path: `/api/v1/workflows/${workflow.id}/start`, method: "POST" },
      { path: `/api/v1/workflows/${workflow.id}`, method: "DELETE" },
    ]);
  });

  it("keeps media credentials in headers and validates private bytes", async () => {
    const secret = "private-mobile-session";
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      expect(input.toString()).not.toContain(secret);
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${secret}`);
      return new Response(new Uint8Array([137, 80, 78, 71]), {
        status: 200,
        headers: {
          "Content-Length": "4",
          "X-Asset-Media-Type": "image/png",
        },
      });
    });
    const client = new MobileApiClient(secret, {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    const content = await client.downloadAsset(asset.id);
    expect(content.mediaType).toBe("image/png");
    expect(Array.from(content.bytes)).toEqual([137, 80, 78, 71]);
  });

  it("uses UUID idempotency headers for generated image and voice media", async () => {
    const message = {
      id: "88888888-8888-4888-8888-888888888888",
      conversation_id: conversationId,
      role: "assistant",
      content: "Generated private image.",
      sequence_number: 1,
      created_at: timestamp,
      updated_at: timestamp,
      attachments: [],
      citations: [],
    };
    const idempotencyKeys: string[] = [];
    const fetchMock = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      idempotencyKeys.push(headers.get("Idempotency-Key") ?? "");
      return input.toString().endsWith("/voice/syntheses")
        ? new Response(JSON.stringify({ asset: { ...asset, media_type: "audio/wav", provenance_kind: "speech_synthesis" }, created: true }))
        : new Response(JSON.stringify({ asset, message, created: true }));
    });
    const client = new MobileApiClient("mobile-session", {
      baseUrl: "https://work-station.example.ts.net",
      fetchImplementation: fetchMock,
    });

    await client.generateImage({ conversation_id: conversationId, model_id: "image-model", prompt: "Private landscape" });
    await client.synthesizeVoice({ model_id: "voice-model", text: "Private playback" });
    expect(idempotencyKeys).toHaveLength(2);
    for (const key of idempotencyKeys) {
      expect(key).toMatch(/^[0-9a-f-]{36}$/);
    }
  });
});
