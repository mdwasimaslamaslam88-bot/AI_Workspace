import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  normalizeApiBaseUrl,
} from "../src/api/client";
import {
  conversation,
  errorEnvelope,
  jsonResponse,
  message,
  model,
  rawSecret,
  token,
  user,
} from "./fixtures";

describe("ApiClient", () => {
  it("sends the bearer only in Authorization and preserves the request shape", async () => {
    const fetchImplementation = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const url = input.toString();
      expect(url).toBe(
        `http://127.0.0.1:8000/api/v1/conversations?` +
          "limit=50&cursor_updated_at=2026-01-02T00%3A00%3A00Z" +
          "&cursor_id=22222222-2222-4222-8222-222222222222",
      );
      expect(url).not.toContain(token);
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
      return jsonResponse({ items: [conversation], next_cursor: null });
    });

    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    await client.listConversations({
      limit: 50,
      cursor: {
        updated_at: conversation.updated_at,
        id: conversation.id,
      },
    });
    expect(fetchImplementation).toHaveBeenCalledOnce();
  });

  it("uses the exact create, message, and generation contracts", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const fetchImplementation = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      const url = input.toString();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });
      if (url.endsWith("/api/v1/conversations")) {
        return jsonResponse({
          ...conversation,
          initial_message: message(1, "user", "hello"),
        }, 201);
      }
      if (url.includes("/messages/generate")) {
        return jsonResponse({
          model_id: model.model_id,
          message: message(2, "assistant", "hi"),
        }, 201);
      }
      return jsonResponse({
        items: [message(1, "user", "hello")],
        next_cursor: 1,
      });
    });
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await client.createConversation({
      title: "Title",
      system_prompt: "System",
      initial_message: "hello",
    });
    await client.listMessages(conversation.id, { limit: 100, cursor: 1 });
    await client.generateResponse(conversation.id, {
      model_id: model.model_id,
      user_message: "next",
    });

    expect(calls[0]?.body).toEqual({
      title: "Title",
      system_prompt: "System",
      initial_message: "hello",
    });
    expect(calls[1]?.url).toContain("/messages?limit=100&cursor=1");
    expect(calls[2]?.body).toEqual({
      model_id: model.model_id,
      user_message: "next",
    });
  });

  it("clears authentication on 401 and never exposes backend details or the token", async () => {
    const onUnauthorized = vi.fn();
    const client = new ApiClient(token, {
      onUnauthorized,
      fetchImplementation: vi.fn(async () =>
        jsonResponse(errorEnvelope(), 401, "request-401"),
      ) as typeof fetch,
    });

    const error = await client.getCurrentUser().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      kind: "authentication",
      status: 401,
      requestId: "request-401",
      message: "Authentication failed.",
    });
    expect(String(error)).not.toContain(rawSecret);
    expect(String(error)).not.toContain(token);
    expect(String(error)).not.toContain("11434");
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it.each([
    [409, "conflict"],
    [413, "too_large"],
    [429, "busy"],
    [500, "server"],
    [503, "unavailable"],
  ] as const)("normalizes HTTP %i without reflecting raw details", async (status, kind) => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () =>
        jsonResponse(errorEnvelope(), status),
      ) as typeof fetch,
    });
    const error = await client.listModels().catch((caught: unknown) => caught);
    expect(error).toMatchObject({ kind, status });
    expect(String(error)).not.toContain(rawSecret);
    expect(String(error)).not.toContain("11434");
  });

  it("normalizes network failure without including the thrown exception", async () => {
    const client = new ApiClient(token, {
      fetchImplementation: vi.fn(async () => {
        throw new Error(rawSecret);
      }) as typeof fetch,
    });
    const error = await client.getCurrentUser().catch((caught: unknown) => caught);
    expect(error).toMatchObject({
      kind: "network",
      message: "Could not reach the local backend.",
    });
    expect(String(error)).not.toContain(rawSecret);
  });

  it("passes AbortSignal and reports cancellation without fabricating a response", async () => {
    const controller = new AbortController();
    const fetchImplementation = vi.fn(
      (_input: URL | RequestInfo, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    const request = client.getCurrentUser(controller.signal);
    controller.abort();

    await expect(request).rejects.toMatchObject({
      kind: "cancelled",
      message: "Request cancelled.",
    });
    expect(fetchImplementation.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("parses current user and safe public model fields", async () => {
    const fetchImplementation = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(user))
      .mockResolvedValueOnce(jsonResponse({ items: [model] }));
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });
    await expect(client.getCurrentUser()).resolves.toEqual(user);
    await expect(client.listModels()).resolves.toEqual({ items: [model] });
  });

  it("accepts only a secret-free HTTP origin as the configured base URL", () => {
    expect(normalizeApiBaseUrl(undefined)).toBe("http://127.0.0.1:8000");
    expect(normalizeApiBaseUrl("http://localhost:8000/")).toBe(
      "http://localhost:8000",
    );
    expect(() => normalizeApiBaseUrl("http://user:pass@localhost:8000")).toThrow();
    expect(() => normalizeApiBaseUrl("http://localhost:8000/api")).toThrow();
  });
});
