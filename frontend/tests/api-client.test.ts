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

class FakeXmlHttpRequest {
  method = "";
  url = "";
  responseType = "";
  response: unknown = null;
  status = 0;
  body: Document | XMLHttpRequestBodyInit | null = null;
  readonly requestHeaders = new Map<string, string>();
  readonly responseHeaders = new Map<string, string>();
  readonly upload = {
    onprogress: null as ((event: ProgressEvent) => void) | null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string) {
    this.requestHeaders.set(name, value);
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders.get(name) ?? null;
  }

  send(body: Document | XMLHttpRequestBodyInit | null) {
    this.body = body;
  }

  abort() {
    this.onabort?.();
  }
}

describe("ApiClient", () => {
  it("invokes fetch with the browser global receiver", async () => {
    const receiverSensitiveFetch = function (this: unknown): Promise<Response> {
      if (this !== globalThis) {
        return Promise.reject(new TypeError("Invalid fetch receiver"));
      }
      return Promise.resolve(jsonResponse(user));
    } as typeof fetch;
    const client = new ApiClient(token, {
      fetchImplementation: receiverSensitiveFetch,
    });

    await expect(client.getCurrentUser()).resolves.toEqual(user);
  });

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

  it("lists archived conversations only when requested and updates owner state", async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchImplementation = vi.fn(async (input: URL | RequestInfo, init?: RequestInit) => {
      calls.push({
        url: input.toString(),
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return input.toString().includes("include_archived")
        ? jsonResponse({ items: [conversation], next_cursor: null })
        : jsonResponse({ ...conversation, is_pinned: true });
    });
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await client.listConversations({ includeArchived: true });
    await client.updateConversationState(conversation.id, { is_pinned: true });

    expect(calls).toEqual([
      {
        url: "http://127.0.0.1:8000/api/v1/conversations?include_archived=true",
        method: "GET",
        body: undefined,
      },
      {
        url: `http://127.0.0.1:8000/api/v1/conversations/${conversation.id}/state`,
        method: "PATCH",
        body: { is_pinned: true },
      },
    ]);
  });

  it("keeps private conversation search terms in an authenticated POST body", async () => {
    const privateQuery = "private accelerator roadmap";
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(input.toString()).toBe(
          "http://127.0.0.1:8000/api/v1/conversations/search",
        );
        expect(input.toString()).not.toContain(privateQuery);
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        expect(JSON.parse(String(init?.body))).toEqual({
          query: privateQuery,
          limit: 50,
          include_archived: true,
        });
        return jsonResponse({ items: [conversation], next_cursor: null });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(
      client.searchConversations({
        query: privateQuery,
        limit: 50,
        include_archived: true,
      }),
    ).resolves.toEqual({ items: [conversation], next_cursor: null });
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
      attachment_ids: ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
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
      attachment_ids: ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
    });
  });

  it("renames and deletes only the encoded owned conversation route", async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        calls.push({
          url: input.toString(),
          method: init?.method ?? "GET",
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return init?.method === "DELETE"
          ? new Response(null, { status: 204 })
          : jsonResponse({ ...conversation, title: "Renamed" });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(
      client.renameConversation(conversation.id, { title: "Renamed" }),
    ).resolves.toMatchObject({ title: "Renamed" });
    await expect(client.deleteConversation(conversation.id)).resolves.toBeUndefined();

    expect(calls).toEqual([
      {
        url: `http://127.0.0.1:8000/api/v1/conversations/${conversation.id}`,
        method: "PATCH",
        body: { title: "Renamed" },
      },
      {
        url: `http://127.0.0.1:8000/api/v1/conversations/${conversation.id}`,
        method: "DELETE",
        body: undefined,
      },
    ]);
  });

  it("creates immutable owner branches without putting message content in URLs", async () => {
    const editedContent = "  private edited prompt  ";
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        expect(url).toBe(
          `http://127.0.0.1:8000/api/v1/conversations/${conversation.id}/fork`,
        );
        expect(url).not.toContain(editedContent.trim());
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        expect(init?.method).toBe("POST");
        expect(JSON.parse(String(init?.body))).toEqual({
          through_sequence_number: 3,
          replacement_content: editedContent,
        });
        return jsonResponse({ ...conversation, title: "Local chat (copy)" }, 201);
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(
      client.forkConversation(conversation.id, {
        through_sequence_number: 3,
        replacement_content: editedContent,
      }),
    ).resolves.toMatchObject({ title: "Local chat (copy)" });
  });

  it("uses bounded voice contracts and keeps text and credentials out of URLs", async () => {
    const modelId = "piper:" + "b".repeat(24);
    const assetId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const idempotencyKey = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const calls: Array<{ url: string; headers: Headers; body: unknown }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        calls.push({
          url,
          headers: new Headers(init?.headers),
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        if (url.endsWith("/transcriptions")) {
          return jsonResponse({
            text: "local transcript",
            language: "en",
            duration_seconds: 1.5,
          }, 201);
        }
        return jsonResponse({
          created: true,
          asset: {
            id: assetId,
            original_filename: "local-speech.wav",
            media_type: "audio/wav",
            byte_size: 128,
            content_sha256: "d".repeat(64),
            provenance_kind: "speech_synthesis",
            source_asset_id: null,
            runtime_id: "piper",
            model_id: modelId,
            created_at: "2026-08-23T00:00:00Z",
            deleted_at: null,
          },
        }, 201);
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await client.transcribeVoice({ asset_id: assetId, model_id: modelId });
    const synthesis = await client.synthesizeVoice(
      { model_id: modelId, text: "private spoken response" },
      idempotencyKey,
    );

    expect(synthesis.asset.provenance_kind).toBe("speech_synthesis");
    expect(calls[1]?.headers.get("Idempotency-Key")).toBe(idempotencyKey);
    expect(calls[1]?.headers.get("Authorization")).toBe(`Bearer ${token}`);
    expect(calls[1]?.body).toEqual({
      model_id: modelId,
      text: "private spoken response",
    });
    expect(calls[1]?.url).not.toContain("private spoken response");
    expect(calls[1]?.url).not.toContain(token);
  });

  it("uses idempotent bounded image contracts and decodes message provenance", async () => {
    const modelId = "comfyui:" + "a".repeat(24);
    const sourceId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const outputId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const key = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
    const calls: Array<{ url: string; headers: Headers; body: unknown }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        const editing = url.endsWith("/edits");
        calls.push({
          url,
          headers: new Headers(init?.headers),
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        const asset = {
          id: outputId,
          original_filename: editing
            ? "local-image-edit.png"
            : "local-image.png",
          media_type: "image/png",
          byte_size: 128,
          content_sha256: "d".repeat(64),
          provenance_kind: editing ? "image_editing" : "image_generation",
          source_asset_id: editing ? sourceId : null,
          runtime_id: "comfyui",
          model_id: modelId,
          created_at: "2026-08-23T00:00:00Z",
          deleted_at: null,
        };
        return jsonResponse(
          {
            asset,
            message: {
              ...message(2, "assistant", editing
                ? "Edited an image locally."
                : "Generated an image locally."),
              attachments: [
                {
                  id: outputId,
                  position: 1,
                  state: "active",
                  original_filename: asset.original_filename,
                  media_type: "image/png",
                  byte_size: 128,
                  provenance_kind: asset.provenance_kind,
                  source_asset_id: asset.source_asset_id,
                },
              ],
            },
            created: true,
          },
          201,
        );
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await client.generateImage(
      {
        conversation_id: conversation.id,
        model_id: modelId,
        prompt: "private local image prompt",
        width: 768,
        height: 768,
        steps: 20,
        seed: 7,
      },
      key,
    );
    const edit = await client.editImage(
      {
        conversation_id: conversation.id,
        model_id: modelId,
        source_asset_id: sourceId,
        instruction: "private local edit instruction",
        denoise: 0.65,
        seed: 9,
      },
      key,
    );

    expect(edit.asset.provenance_kind).toBe("image_editing");
    expect(edit.message.attachments[0]?.source_asset_id).toBe(sourceId);
    expect(calls[0]?.headers.get("Idempotency-Key")).toBe(key);
    expect(calls[1]?.headers.get("Authorization")).toBe(`Bearer ${token}`);
    expect(calls[0]?.body).toMatchObject({
      prompt: "private local image prompt",
      width: 768,
      height: 768,
      steps: 20,
    });
    expect(calls[1]?.body).toMatchObject({
      source_asset_id: sourceId,
      instruction: "private local edit instruction",
      denoise: 0.65,
    });
    expect(calls[0]?.url).not.toContain("private local image prompt");
    expect(calls[1]?.url).not.toContain("private local edit instruction");
    expect(calls[0]?.url).not.toContain(token);
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

  it("rotates the bearer without placing either credential in the URL", async () => {
    const rotatedToken = "r".repeat(43);
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        expect(input.toString()).toBe(
          "http://127.0.0.1:8000/api/v1/users/me/access-token/rotate",
        );
        expect(input.toString()).not.toContain(token);
        expect(input.toString()).not.toContain(rotatedToken);
        expect(init?.method).toBe("POST");
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        return jsonResponse({ access_token: rotatedToken, token_type: "bearer" });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.rotateAccessToken()).resolves.toEqual({
      access_token: rotatedToken,
      token_type: "bearer",
    });
  });

  it("manages bounded device sessions without sending credentials in URLs", async () => {
    const issuedToken = "s".repeat(43);
    const session = {
      id: "7b914edf-a46b-470c-b3de-9c6109db3fc0",
      label: "Phone",
      created_at: "2026-08-20T09:00:00Z",
      updated_at: "2026-08-21T09:00:00Z",
      is_current: false,
    };
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = new URL(input.toString());
        expect(url.toString()).not.toContain(token);
        expect(url.toString()).not.toContain(issuedToken);
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          `Bearer ${token}`,
        );
        calls.push({
          path: url.pathname,
          method: init?.method ?? "GET",
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        if (init?.method === "DELETE") return new Response(null, { status: 204 });
        if (init?.method === "POST") {
          return jsonResponse({
            access_token: issuedToken,
            token_type: "bearer",
            session,
          });
        }
        if (init?.method === "PATCH") return jsonResponse({ ...session, is_current: true });
        return jsonResponse({ items: [session] });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.listUserSessions()).resolves.toEqual({ items: [session] });
    await expect(client.createUserSession({ label: "Phone" })).resolves.toEqual({
      access_token: issuedToken,
      token_type: "bearer",
      session,
    });
    await expect(
      client.renameCurrentUserSession({ label: "Browser" }),
    ).resolves.toEqual({ ...session, is_current: true });
    await client.revokeUserSession(session.id);
    await client.revokeCurrentUserSession();

    expect(calls).toEqual([
      { path: "/api/v1/users/me/sessions", method: "GET", body: undefined },
      { path: "/api/v1/users/me/sessions", method: "POST", body: { label: "Phone" } },
      { path: "/api/v1/users/me/sessions/current", method: "PATCH", body: { label: "Browser" } },
      { path: `/api/v1/users/me/sessions/${session.id}`, method: "DELETE", body: undefined },
      { path: "/api/v1/users/me/sessions/current", method: "DELETE", body: undefined },
    ]);
  });

  it("accepts only a secret-free HTTP origin as the configured base URL", () => {
    expect(normalizeApiBaseUrl(undefined)).toBe("http://127.0.0.1:8000");
    expect(
      normalizeApiBaseUrl(undefined, "https://work-station.example.ts.net"),
    ).toBe("https://work-station.example.ts.net");
    expect(normalizeApiBaseUrl(undefined, "http://localhost:3000")).toBe(
      "http://127.0.0.1:8000",
    );
    expect(normalizeApiBaseUrl(undefined, "http://localhost:8000")).toBe(
      "http://localhost:8000",
    );
    expect(normalizeApiBaseUrl("http://localhost:8000/")).toBe(
      "http://localhost:8000",
    );
    expect(() => normalizeApiBaseUrl("http://user:pass@localhost:8000")).toThrow();
    expect(() => normalizeApiBaseUrl("http://localhost:8000/api")).toThrow();
    expect(() => normalizeApiBaseUrl("http://192.0.2.1:8000")).toThrow(
      "must use HTTPS",
    );
  });

  it("uploads one file with bearer, idempotency, and progress without setting multipart content type", async () => {
    const xhr = new FakeXmlHttpRequest();
    const onProgress = vi.fn();
    const client = new ApiClient(token, {
      xhrFactory: () => xhr as unknown as XMLHttpRequest,
    });
    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const idempotencyKey = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const request = client.uploadAsset(file, idempotencyKey, {
      onProgress,
    });

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("http://127.0.0.1:8000/api/v1/assets");
    expect(xhr.url).not.toContain(token);
    expect(xhr.requestHeaders.get("Authorization")).toBe(`Bearer ${token}`);
    expect(xhr.requestHeaders.get("Idempotency-Key")).toBe(idempotencyKey);
    expect(xhr.requestHeaders.get("Content-Type")).toBeUndefined();
    expect(xhr.body).toBeInstanceOf(FormData);
    expect((xhr.body as FormData).get("file")).toBe(file);

    xhr.upload.onprogress?.(
      {
        loaded: 5,
        total: 10,
        lengthComputable: true,
      } as unknown as ProgressEvent,
    );
    expect(onProgress).toHaveBeenCalledWith({ loaded: 5, total: 10 });

    const asset = {
      id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      original_filename: "hello.txt",
      media_type: "text/plain",
      byte_size: 5,
      content_sha256: "a".repeat(64),
      provenance_kind: "upload",
      source_asset_id: null,
      runtime_id: null,
      model_id: null,
      created_at: "2026-01-03T00:00:00Z",
      deleted_at: null,
    };
    xhr.status = 201;
    xhr.response = asset;
    xhr.responseHeaders.set("X-Request-ID", "upload-request");
    xhr.onload?.();

    await expect(request).resolves.toEqual(asset);
    expect(onProgress).toHaveBeenCalledOnce();
  });

  it("uses owner-scoped document endpoints and decodes safe citation provenance", async () => {
    const document = {
      id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      asset_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      status: "ready",
      source_state: "active",
      original_filename: "notes.txt",
      media_type: "text/plain",
      chunk_count: 1,
      character_count: 5,
      failure_code: null,
      created_at: "2026-01-03T00:00:00Z",
      updated_at: "2026-01-03T00:00:01Z",
      completed_at: "2026-01-03T00:00:01Z",
    };
    const citedMessage = {
      ...message(2, "assistant", "grounded"),
      citations: [
        {
          asset_id: document.asset_id,
          position: 1,
          state: "active",
          original_filename: "notes.txt",
          page_number: null,
          row_start: 2,
          row_end: 3,
          section: null,
          excerpt: "bounded excerpt",
        },
      ],
    };
    const calls: Array<{ url: string; method: string | undefined }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        calls.push({ url, method: init?.method });
        if (url.endsWith("/ingest")) return jsonResponse(document, 202);
        if (url.includes("/messages?")) {
          return jsonResponse({ items: [citedMessage], next_cursor: null });
        }
        return jsonResponse(document);
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    await expect(client.ingestDocument(document.asset_id)).resolves.toEqual(document);
    await expect(client.getDocument(document.id)).resolves.toEqual(document);
    const page = await client.listMessages(conversation.id, { limit: 1 });

    expect(calls[0]).toEqual({
      url: `http://127.0.0.1:8000/api/v1/documents/assets/${document.asset_id}/ingest`,
      method: "POST",
    });
    expect(calls[1]?.url).toBe(
      `http://127.0.0.1:8000/api/v1/documents/${document.id}`,
    );
    expect(page.items[0]?.citations[0]).toMatchObject({
      original_filename: "notes.txt",
      row_start: 2,
      row_end: 3,
      excerpt: "bounded excerpt",
    });
  });

  it("uses explicit memory CRUD/settings contracts and decodes tombstones", async () => {
    const activeMemory = {
      id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      category: "instruction",
      state: "active",
      content: "Always show steps.",
      provenance_kind: "explicit_user_entry",
      created_at: "2026-08-22T00:00:00Z",
      updated_at: "2026-08-22T00:00:00Z",
      deleted_at: null,
    };
    const deletedMemory = {
      ...activeMemory,
      state: "deleted",
      content: null,
      deleted_at: "2026-08-22T01:00:00Z",
      updated_at: "2026-08-22T01:00:00Z",
    };
    const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
    const fetchImplementation = vi.fn(
      async (input: URL | RequestInfo, init?: RequestInit) => {
        const url = input.toString();
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, method: init?.method, body });
        if (url.endsWith("/settings")) {
          return jsonResponse({ enabled: false, created_at: null, updated_at: null });
        }
        if (init?.method === "POST") return jsonResponse(activeMemory, 201);
        if (init?.method === "DELETE") return jsonResponse(deletedMemory);
        return jsonResponse({ items: [activeMemory, deletedMemory] });
      },
    );
    const client = new ApiClient(token, {
      fetchImplementation: fetchImplementation as typeof fetch,
    });

    const page = await client.listMemories({ includeDeleted: true });
    await client.createMemory({
      category: "instruction",
      content: "Always show steps.",
    });
    const forgotten = await client.forgetMemory(activeMemory.id);
    const setting = await client.updateMemorySetting(false);

    expect(page.items[1]).toEqual(deletedMemory);
    expect(forgotten.content).toBeNull();
    expect(setting.enabled).toBe(false);
    expect(calls[0]?.url).toBe(
      "http://127.0.0.1:8000/api/v1/memories?include_deleted=true",
    );
    expect(calls[1]).toMatchObject({
      method: "POST",
      body: { category: "instruction", content: "Always show steps." },
    });
    expect(calls[2]?.method).toBe("DELETE");
    expect(calls[3]).toMatchObject({ method: "PUT", body: { enabled: false } });
  });
});
