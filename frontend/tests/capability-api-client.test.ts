import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "../src/api/client";
import {
  jsonResponse,
  productCapabilities,
  token,
} from "./fixtures";

describe("product capability API client", () => {
  it("loads the exact authenticated capability snapshot", async () => {
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(input.toString()).toBe(
          "http://127.0.0.1:8000/api/v1/ai/capabilities",
        );
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        return jsonResponse({ items: productCapabilities });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.getProductCapabilities()).resolves.toEqual({
      items: productCapabilities,
    });
  });

  it("rejects duplicated or lifecycle-inconsistent capability data", async () => {
    const inconsistent = productCapabilities.map((item, index) =>
      index === 0
        ? {
            ...item,
            status: "available",
            blocking_reasons: ["asset_storage_required"],
          }
        : item,
    );
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse({ items: inconsistent }),
      ) as typeof fetch,
    });

    await expect(client.getProductCapabilities()).rejects.toMatchObject({
      kind: "unexpected",
    });

    const duplicated = productCapabilities.map((item, index) =>
      index === 1 ? { ...item, id: productCapabilities[0].id } : item,
    );
    const duplicateClient = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse({ items: duplicated }),
      ) as typeof fetch,
    });
    await expect(duplicateClient.getProductCapabilities()).rejects.toMatchObject({
      kind: "unexpected",
    });
  });
});
