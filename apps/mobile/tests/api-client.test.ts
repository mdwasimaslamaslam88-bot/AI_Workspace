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
});
