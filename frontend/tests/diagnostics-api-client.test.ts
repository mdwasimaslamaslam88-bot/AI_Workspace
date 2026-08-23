import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import { jsonResponse, systemDiagnostics, token } from "./fixtures";

describe("private diagnostics API client", () => {
  it("loads a bounded authenticated diagnostic snapshot", async () => {
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(input.toString()).toBe(
          "http://127.0.0.1:8000/api/v1/diagnostics",
        );
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        return jsonResponse(systemDiagnostics);
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.getSystemDiagnostics()).resolves.toEqual(
      systemDiagnostics,
    );
  });

  it("rejects duplicate services and unbounded GPU data", async () => {
    const invalid = {
      ...systemDiagnostics,
      services: systemDiagnostics.services.map((item, index) =>
        index === 1 ? { ...item, id: "backend" } : item,
      ),
      gpus: [{ ...systemDiagnostics.gpus[0], vram_bytes: -1 }],
    };
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => jsonResponse(invalid)) as typeof fetch,
    });

    await expect(client.getSystemDiagnostics()).rejects.toMatchObject({
      kind: "unexpected",
    });
  });
});
