import { describe, expect, it, vi } from "vitest";

import {
  MobileApiClient,
  MobileApiError,
  normalizeMobileApiBaseUrl,
} from "../src/api/client";

const user = {
  id: "11111111-1111-4111-8111-111111111111",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
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
});
